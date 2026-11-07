"""
Builds the DooM hub: a static site that lists every project repo in the
mars-doom GitHub org and republishes each project's docs pages inside the hub.

    python build.py            # real repos only
    python build.py --demo     # plus a few example cards, to see a fuller gallery

Output goes to site/. Standard library only. Set GITHUB_TOKEN to lift the API
rate limit from 60 to 5,000 requests an hour (Actions provides one for free).
"""
import csv
import hashlib
import html
import io
import json
import os
import posixpath
import re
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ORG = "mars-doom"
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

# Repos that are never projects, even though they live in the org.
SKIP = {".github", "doom-template", f"{ORG}.github.io"}

# Words in the parts list's cost column that mean "cost nothing" (as tools/makers.py).
FREE_WORDS = {"free", "salvaged", "salvage", "scavenged", "gifted", "donated",
              "found", "reused", "scrap", "repurposed"}
HEADER_LABELS = {"qty": "Qty", "cost_each": "Cost each", "item": "Item",
                 "details": "Details", "source": "Source", "notes": "Notes"}

# MkDocs admonition types, as the GitHub alert that looks most like each.
ALERTS = {"note": "NOTE", "info": "NOTE", "abstract": "NOTE", "summary": "NOTE",
          "tip": "TIP", "hint": "TIP", "success": "TIP", "example": "TIP",
          "important": "IMPORTANT", "question": "IMPORTANT",
          "warning": "WARNING", "caution": "WARNING", "attention": "WARNING",
          "danger": "CAUTION", "error": "CAUTION", "failure": "CAUTION", "bug": "CAUTION"}


# ---- Fetching -------------------------------------------------------------

def get(url, data=None, raw=False):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "doom-hub"}
    if os.environ.get("GITHUB_TOKEN") and url.startswith(API):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    if data is not None:
        data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data, headers)) as r:
            body = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return body if raw else json.loads(body)


def repo_file(repo, path):
    return get(f"{RAW}/{ORG}/{repo['name']}/{repo['default_branch']}/{quote(path)}", raw=True)


def render_markdown(text, repo):
    """GitHub renders the markdown, so it looks just like the repo does."""
    if not text.strip():
        return ""
    return get(f"{API}/markdown", {"text": text, "mode": "gfm",
                                   "context": repo["full_name"]}, raw=True)


# ---- Turning MkDocs pages into hub pages ----------------------------------

def strip_front_matter(md):
    return re.sub(r"\A---[ \t]*\n.*?\n---[ \t]*\n", "", md or "", count=1, flags=re.S)


def strip_comments(md):
    return re.sub(r"<!--.*?-->", "", md, flags=re.S)


def page_slug(path):
    """docs/how-it-works.md -> how-it-works; docs/index.md -> index."""
    return re.sub(r"\.md$", "", path).replace("/", "-")


def fix_links(md, repo, pages):
    """Rewrite the relative links in a page that lives in docs/.

    Images load from the raw repo, links to other doc pages stay inside the hub,
    and links to anything else in the repo (files/, parts.csv) open on GitHub.
    """
    raw = f"{RAW}/{ORG}/{repo['name']}/{repo['default_branch']}/"
    blob = f"{repo['html_url']}/blob/{repo['default_branch']}/"

    def link(m):
        bang, text, target, title = m[1], m[2], m[3], m[4] or ""
        if re.match(r"[a-z]+:|#|/", target):
            return m[0]
        path, _, anchor = target.partition("#")
        path = posixpath.normpath(posixpath.join("docs", path))
        if bang:
            url = raw + quote(path)
        elif path.endswith(".md") and path.startswith("docs/") and page_slug(path[5:]) in pages:
            url = f"{page_slug(path[5:])}.html"
        else:
            url = blob + quote(path)
        return f"{bang}[{text}]({url}{'#' + anchor if anchor else ''}{title})"

    return re.sub(r"(!?)\[([^\]]*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)", link, md)


def admonitions(md):
    """MkDocs `!!! warning "Title"` blocks become GitHub alerts."""
    def alert(m):
        kind = ALERTS.get(m[1].lower(), "NOTE")
        body = re.sub(r"^ {4}", "", m[3], flags=re.M).strip("\n")
        lines = ([f"**{m[2]}**"] if m[2] else []) + body.split("\n")
        return f"> [!{kind}]\n" + "\n".join(f"> {l}".rstrip() for l in lines) + "\n\n"
    return re.sub(r"^[!?]{3}\+? *(\w+)(?: +\"([^\"]*)\")?[^\n]*\n((?:(?: {4}.*)?\n)*)", alert, md, flags=re.M)


def strip_attr_lists(md):
    """Drop MkDocs `{ width=300 }` / `{ #id .class }` attributes GitHub would print."""
    return re.sub(r"\{:? *(?:[#.][\w-]+|\w+=[^}\n]*)[^}\n]*\}", "", md)


def heading_ids(rendered):
    """GitHub's renderer leaves headings without ids; add them, slugged the way
    MkDocs does, so #links written for the docs site land in the same place."""
    def add(m):
        text = html.unescape(re.sub(r"<[^>]+>", "", m[3]))
        slug = re.sub(r"[-\s]+", "-", re.sub(r"[^\w\s-]", "", text).strip().lower())
        return f'<h{m[1]} id="{slug}"{m[2]}>{m[3]}</h{m[1]}>'
    return re.sub(r"<h([1-6])((?: [^>]*)?)>(.*?)</h\1>", add, rendered, flags=re.S)


def render_page(md, repo, pages, context):
    """One docs page, MkDocs-isms and all, to finished HTML."""
    md = strip_comments(strip_front_matter(md))
    md = md.replace("{{ parts_list }}", parts_markdown(context["parts"]))
    md = md.replace("{{ files_list }}", files_markdown(context["files"], repo))
    md = strip_attr_lists(admonitions(md))
    md = fix_links(md, repo, pages)

    # Mermaid goes around GitHub's renderer, which wraps it in GitHub-only markup.
    diagrams = []
    def stash(m):
        diagrams.append(m[1])
        return f"\n\nMERMAID{len(diagrams) - 1}\n\n"
    md = re.sub(r"^```mermaid[^\n]*\n(.*?)^```[ \t]*$", stash, md, flags=re.M | re.S)

    out = heading_ids(render_markdown(md, repo))
    for i, src in enumerate(diagrams):
        out = re.sub(rf"<p[^>]*>MERMAID{i}</p>", f'<pre class="mermaid">{html.escape(src)}</pre>', out)
    return out


# ---- Parts and files lists (as tools/makers.py builds them) ---------------

def _cost(value):
    v = (value or "").strip().lower()
    if not v:
        return None
    if v in FREE_WORDS:
        return 0.0
    try:
        return float(v.replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _qty(value):
    try:
        return float((value or "").strip() or "1")
    except ValueError:
        return 1.0


def read_parts(text):
    rows = [r for r in csv.reader(io.StringIO((text or "").lstrip("\ufeff"))) if any(c.strip() for c in r)]
    if len(rows) < 2:
        return [], []
    header = [h.strip() for h in rows[0]]
    return header, [(r + [""] * len(header))[:len(header)] for r in rows[1:]]


def parts_total(header, rows):
    keys = [h.lower() for h in header]
    if "cost_each" not in keys:
        return 0.0
    ci, qi = keys.index("cost_each"), keys.index("qty") if "qty" in keys else None
    return sum((_cost(r[ci]) or 0) * (_qty(r[qi]) if qi is not None else 1) for r in rows)


def parts_markdown(parts):
    header, body = parts
    if not body:
        return "*No parts listed yet.*"
    keys = [h.lower() for h in header]
    keep = [i for i, k in enumerate(keys) if k == "section" or any(r[i].strip() for r in body)]
    header, body = [header[i] for i in keep], [[r[i] for i in keep] for r in body]
    keys = [h.lower() for h in header]

    sections = {}
    if "section" in keys:
        si = keys.index("section")
        for r in body:
            sections.setdefault(r[si].strip() or "Other", []).append([c for i, c in enumerate(r) if i != si])
        header = [h for i, h in enumerate(header) if i != si]
    else:
        sections[""] = body
    keys = [h.lower() for h in header]

    def cell(key, value):
        value = value.replace("|", "\\|").replace("\n", " ").strip()
        n = _cost(value) if key == "cost_each" else None
        return f"${n:,.2f}" if n else value

    out = []
    for name, rows in sections.items():
        if name and not (len(sections) == 1 and name == "Other"):
            out += [f"### {name}", ""]
        out += ["| " + " | ".join(HEADER_LABELS.get(k, h.replace("_", " ").capitalize()) for k, h in zip(keys, header)) + " |",
                "|" + "|".join(" --- " for _ in keys) + "|"]
        out += ["| " + " | ".join(cell(k, c) for k, c in zip(keys, r)) + " |" for r in rows]
        out.append("")
    total = parts_total(header, [r for rows in sections.values() for r in rows])
    if total:
        out.append(f"**Total: ${total:,.2f}**")
    return "\n".join(out)


def _size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def files_markdown(files, repo):
    if not files:
        return "*No design files yet.*"
    blob = f"{repo['html_url']}/blob/{repo['default_branch']}/"
    return "\n".join(f"- [{f['path'][6:]}]({blob}{quote(f['path'])}) · {_size(f['size'])}" for f in files)


# ---- Reading a project ----------------------------------------------------

def field(md, name):
    m = re.search(rf"\*\*{name}:\*\*\s*(.+)", strip_comments(md))
    return m[1].strip() if m else None


def nav_pages(mkdocs, tree):
    """The docs pages in menu order: mkdocs.yml's nav, else every .md in docs/."""
    block = re.search(r"^nav:[ \t]*\n((?:[ \t]+.*\n?|\n)+)", mkdocs, re.M)
    found = re.findall(r"^\s*-\s*(?:['\"]?([^:'\"\n]+)['\"]?\s*:\s*)?([\w./-]+\.md)\s*$", block[1], re.M) if block else []
    if found:
        return [(title.strip() or None, file) for title, file in found]
    files = sorted(t["path"][5:] for t in tree if t["path"].startswith("docs/") and t["path"].endswith(".md"))
    return [(None, f) for f in sorted(files, key=lambda f: f != "index.md")]


def read_project(repo):
    branch = repo["default_branch"]
    tree = (get(f"{API}/repos/{repo['full_name']}/git/trees/{branch}?recursive=1") or {}).get("tree", [])
    mkdocs = repo_file(repo, "mkdocs.yml") or ""
    parts = read_parts(repo_file(repo, "parts.csv"))
    files = [t for t in tree if t["type"] == "blob" and t["path"].startswith("files/")
             and not any(p.startswith(".") for p in t["path"].split("/"))
             and posixpath.basename(t["path"]).lower() != "readme.md"]
    context = {"parts": parts, "files": files}

    nav = nav_pages(mkdocs, tree)
    slugs = {page_slug(f) for _, f in nav}
    sources = {f: strip_front_matter(repo_file(repo, f"docs/{f}") or "") for _, f in nav}
    index = sources.get("index.md", "")

    title = re.search(r"^site_name:\s*(.+)$", mkdocs, re.M)
    h1 = re.search(r"^# (.+)$", index, re.M)
    hero = re.search(r"!\[[^\]]*\]\(([^)\s]+)", index)
    author = re.search(r"^site_author:\s*(.+)$", mkdocs, re.M)

    pages = []
    for nav_title, f in nav:
        md = sources[f]
        if f == "index.md":
            # The summary page shows the index without what the header already shows.
            for pattern in (r"^# .+\n", r"!\[[^\]]*\]\([^)]+\)\n", r"\*\*Status:\*\*.*\n",
                            r"\*\*Maker:\*\*.*\n", r"## Pages\n(?:\n|- .*\n)*"):
                md = re.sub(pattern, "", md, count=1, flags=re.M)
        page_h1 = re.search(r"^# (.+)$", md, re.M)
        pages.append({
            "slug": page_slug(f),
            "file": f,
            "title": "Summary" if f == "index.md" else (nav_title or (page_h1[1].strip() if page_h1 else page_slug(f))),
            "html": render_page(md, repo, slugs, context),
        })

    # Newest log entry: the first ### heading and everything up to the next one.
    log = strip_comments(sources.get("log.md", ""))
    entry = re.search(r"^### (.+?)\n(.*?)(?=^### |\Z)", log, re.M | re.S)

    contributors = get(f"{API}/repos/{repo['full_name']}/contributors") or []

    return {
        "slug": repo["name"],
        "name": h1[1].strip() if h1 else (title[1].strip() if title else repo["name"]),
        "description": repo["description"] or "",
        "status": field(index, "Status"),
        "maker": field(index, "Maker") or (author[1].strip() if author else None),
        "hero": hero and (hero[1] if hero[1].startswith("http")
                          else f"{RAW}/{ORG}/{repo['name']}/{branch}/{posixpath.normpath('docs/' + hero[1])}"),
        "pages": pages,
        "log_title": entry[1].strip() if entry else None,
        "log_html": render_page(entry[2], repo, slugs, context) if entry else None,
        "parts_count": len(parts[1]),
        "parts_cost": parts_total(*parts),
        "files_count": len(files),
        "contributors": [{"login": c["login"], "avatar": c["avatar_url"], "url": c["html_url"]}
                         for c in contributors if c.get("type") == "User"],
        "updated": repo["pushed_at"][:10],
        "repo_url": repo["html_url"],
        "edit_url": f"{repo['html_url']}/edit/{branch}/docs/",
        "issues_url": repo["html_url"] + "/issues",
        "license": (repo.get("license") or {}).get("spdx_id"),
    }


def is_project(repo):
    return (repo["name"] not in SKIP and not repo["name"].startswith(".")
            and not repo["is_template"] and not repo["archived"] and not repo["private"])


# ---- Pages ----------------------------------------------------------------

MERMAID = """<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs';
const nodes = [...document.querySelectorAll('pre.mermaid')];
const sources = nodes.map(n => n.textContent);
async function draw() {
  const dark = window.doomTheme ? window.doomTheme() === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  mermaid.initialize({ startOnLoad: false, theme: 'base', themeVariables: {
    fontFamily: 'Montserrat, sans-serif',
    primaryColor: dark ? '#162829' : '#EEECE7', primaryTextColor: dark ? '#E8E3DA' : '#0F4C4F',
    primaryBorderColor: dark ? '#8CC3BF' : '#0F4C4F', lineColor: dark ? '#E08A62' : '#B5552F',
    secondaryColor: dark ? '#1D3334' : '#E6E3DC', tertiaryColor: dark ? '#0F1D1E' : '#F4F2EE' } });
  nodes.forEach((n, i) => { n.removeAttribute('data-processed'); n.textContent = sources[i]; });
  await mermaid.run({ nodes });
}
draw();
document.addEventListener('themechange', draw);
</script>"""

# Set the saved theme before the page paints, so a dark-mode choice doesn't flash light.
THEME_EARLY = ("<script>try{var t=localStorage.getItem('doom-theme');"
               "if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}catch(e){}</script>")

THEME_BUTTON = """<button class="theme-toggle" type="button" aria-label="Switch to dark mode">
      <svg class="moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"/></svg>
      <svg class="sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
    </button>"""


def asset_version(name):
    """A short hash of an asset's contents, added to its link as ?v=... GitHub Pages
    lets browsers reuse CSS and JS for 10 minutes, so without it a new page can load
    with the old stylesheet; with it, a changed file gets a new URL."""
    return hashlib.sha256((ROOT / "assets" / name).read_bytes()).hexdigest()[:10]


def page(title, body, depth=0, description=""):
    up = "../" * depth
    script = MERMAID if 'class="mermaid"' in body else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
{THEME_EARLY}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;1,400&family=Montserrat:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{up}assets/style.css?v={asset_version('style.css')}">
</head>
<body>
<header class="topbar">
  <a class="wordmark" href="{up}index.html" aria-label="DooM: Department of Makers, home">Department <i>of</i> <b>Makers</b></a>
  <nav>
    {THEME_BUTTON}
    <a href="{up}index.html#projects">Projects</a>
    <a href="{up}guide/index.html">Guide</a>
    <a href="{up}shrink/index.html">Shrink photos</a>
    <a href="https://github.com/{ORG}">GitHub</a>
  </nav>
</header>
{body}
<footer class="foot">
  <span class="foot-mark" aria-hidden="true">DooM</span>
  <div>
    <p>Mars College Department of Makers · Hardware CERN-OHL-W-2.0 · Docs and photos CC BY-SA 4.0</p>
    <p class="mono">Built {datetime.now():%Y-%m-%d} from <a href="https://github.com/{ORG}">github.com/{ORG}</a></p>
  </div>
</footer>
<script src="{up}assets/theme.js?v={asset_version('theme.js')}"></script>
{script}
</body>
</html>
"""


def card(p, link, n):
    e = html.escape
    img = f'<img src="{e(p["hero"])}" alt="" loading="lazy" decoding="async">' if p.get("hero") else ""
    status = f'<span class="chip">{e(p["status"])}</span>' if p.get("status") else ""
    who = f'<span class="who">{e(p["maker_short"])}</span>' if p.get("maker_short") else ""
    desc = f'<span class="desc">{e(p["description"])}</span>' if p.get("description") else ""
    cls = ' class="example"' if p.get("example") else ""
    return (f'<li{cls}><a href="{link}"><span class="photo">{img}{status}</span>'
            f'<span class="entry"><span class="num">{n:02d}</span><span class="text">'
            f'<span class="name">{e(p["name"])}</span>{who}{desc}</span></span></a></li>')


def hub(projects):
    cards = "\n".join(card(p, f"projects/{p['slug']}/index.html" if not p.get("example") else "#projects", i)
                      for i, p in enumerate(projects, 1))
    makers = {c["login"] for p in projects for c in p.get("contributors", [])}
    count = sum(1 for p in projects if not p.get("example"))
    return page("DooM · Mars College Department of Makers", f"""
<section class="hero">
  <p class="eyebrow"><a href="https://dom.mars.college/">Mars College 2027</a></p>
  <h1 class="display"><span class="numeral" aria-hidden="true">{count}</span><small>the</small>Projects</h1>
  <p class="lede">Open hardware from the Department of Makers. Every project documents what it built,
  what broke and how to make your own.</p>
  <div class="stats">
    <span><b>{count:02d}</b> projects</span>
    <span><b>{len(makers):02d}</b> makers</span>
    <span><b>2027</b> season</span>
  </div>
</section>

<section id="projects" class="projects">
  <div class="section-head">
    <h2>Projects</h2>
    <p class="label-caps muted">Pulled live from github.com/{ORG}</p>
  </div>
  <ul class="cards">
{cards}
  </ul>
</section>
""", description="Open hardware projects from the Mars College Department of Makers.")


def tabs(p, current):
    here = ' aria-current="page"'
    links = "".join(
        f'<a href="{pg["slug"]}.html"{here if pg["slug"] == current else ""}><i>{i:02d}</i>{html.escape(pg["title"])}</a>'
        for i, pg in enumerate(p["pages"], 1))
    return f'<nav class="tabs" aria-label="Project pages">{links}</nav>'


def project_summary(p, pg):
    e = html.escape
    hero = f'<img class="project-hero" src="{e(p["hero"])}" alt="">' if p["hero"] else ""
    people = "".join(f'<a href="{e(c["url"])}" title="{e(c["login"])}"><img src="{e(c["avatar"])}&s=80" alt="{e(c["login"])}"></a>'
                     for c in p["contributors"])
    cost = f"${p['parts_cost']:,.0f}" if p["parts_cost"] else "not costed yet"
    build = next((x for x in p["pages"] if x["file"].startswith("build")), None)
    has_log = any(x["slug"] == "log" for x in p["pages"])
    log = f"""
    <section class="panel">
      <h2>Latest from the log</h2>
      <p class="label-caps muted">{e(p["log_title"])}</p>
      <div class="prose clamp">{p["log_html"]}</div>
      {'<a href="log.html">Read the full log →</a>' if has_log else ""}
    </section>""" if p["log_html"] else ""
    return page(f"{p['name']} · DooM", f"""
<main class="project">
  <p class="crumbs"><a href="../../index.html#projects">Projects</a> / {e(p["slug"])}</p>
  <div class="project-head">
    <div>
      {f'<span class="chip">{e(p["status"])}</span>' if p["status"] else ""}
      <h1>{e(p["name"])}</h1>
      <p class="lede">{e(p["description"])}</p>
      <p class="maker">{e(p["maker"] or "")}</p>
      <div class="actions">
        {f'<a class="button" href="{build["slug"]}.html">Build it yourself</a>' if build else ""}
        <a class="button ghost" href="{e(p["repo_url"])}">Repository</a>
      </div>
    </div>
    {hero}
  </div>
  {tabs(p, pg["slug"])}

  <div class="project-body">
    <article class="prose">{pg["html"]}</article>
    <aside>
      <section class="panel facts">
        <div><span>Updated</span><b>{e(p["updated"])}</b></div>
        <div><span>Parts</span><b>{p["parts_count"]} line items</b></div>
        <div><span>Est. cost</span><b>{cost}</b></div>
        <div><span>Design files</span><b>{p["files_count"]}</b></div>
        <div><span>License</span><b>{e(p["license"] or "—")}</b></div>
      </section>
      <section class="panel">
        <h2>Contributors</h2>
        <div class="people">{people or '<span class="muted">None yet</span>'}</div>
        <a href="{e(p["issues_url"])}/new/choose">Send a build report or problem →</a>
      </section>
      {log}
    </aside>
  </div>
</main>
""", depth=2, description=p["description"])


def project_doc(p, pg):
    """Any docs page other than the summary: the page itself plus an outline."""
    e = html.escape
    outline = re.findall(r'<h2 id="([^"]*)"[^>]*>(.*?)</h2>', pg["html"], re.S)
    toc = "".join(f'<li><a href="#{i}">{re.sub(r"<[^>]+>", "", t)}</a></li>' for i, t in outline)
    return page(f"{pg['title']} · {p['name']} · DooM", f"""
<main class="project">
  <p class="crumbs"><a href="../../index.html#projects">Projects</a> / <a href="index.html">{e(p["slug"])}</a> / {e(pg["slug"])}</p>
  <div class="project-head compact">
    <div>
      {f'<span class="chip">{e(p["status"])}</span>' if p["status"] else ""}
      <p class="project-name"><a href="index.html">{e(p["name"])}</a></p>
    </div>
  </div>
  {tabs(p, pg["slug"])}

  <div class="project-body">
    <article class="prose doc">{pg["html"] or '<p class="muted">Nothing on this page yet.</p>'}</article>
    <aside>
      {f'<section class="panel toc"><h2>On this page</h2><ul>{toc}</ul></section>' if toc else ""}
    </aside>
  </div>
</main>
""", depth=2, description=p["description"])


def shrink_page():
    """A phone-friendly page that shrinks photos before they're uploaded to a repo."""
    return page("Shrink photos · DooM", """
<main class="project shrink">
  <h1>Shrink photos</h1>
  <p class="lede">Make phone photos small enough for your project page. Your originals
  aren't touched, and nothing is uploaded: it all happens on your phone.</p>

  <ol class="steps">
    <li><span><b>Pick photos.</b> As many as you like.</span></li>
    <li><span><b>Save the small copies.</b> They go to your camera roll next to the originals.</span></li>
    <li><span><b>Upload those</b> to your project in Pages CMS, under Media.</span></li>
  </ol>

  <div class="picker">
    <label class="button" for="pick">Choose photos</label>
    <input id="pick" type="file" accept="image/*" multiple>
    <label class="size mono">Size
      <select id="size">
        <option value="2000" selected>Standard · 2000 px</option>
        <option value="1200">Small · 1200 px</option>
      </select>
    </label>
  </div>

  <div id="actions" class="actions-bar" hidden>
    <span id="summary" class="mono muted"></span>
    <button id="save-all" class="button" type="button">Save all</button>
  </div>
  <ul id="results" class="results"></ul>

  <p class="muted small">Photos come out as JPEGs, with location and camera details removed.</p>
</main>
<script src="../assets/shrink.js?v=""" + asset_version("shrink.js") + """"></script>
""", depth=1, description="Shrink phone photos before uploading them to a DooM project.")


def redirect(to):
    return f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={to}"><link rel="canonical" href="{to}"><a href="{to}">Moved here</a>'


# ---- Example cards (--demo) -----------------------------------------------

EXAMPLES = [
    {"slug": "example-1", "name": "Water Systems", "status": "Example", "maker_short": "Placeholder"},
    {"slug": "example-2", "name": "Solar Kitchen", "status": "Example", "maker_short": "Placeholder"},
    {"slug": "example-3", "name": "Shade Structures", "status": "Example", "maker_short": "Placeholder"},
    {"slug": "example-4", "name": "Tool Library", "status": "Example", "maker_short": "Placeholder"},
]


def main():
    repos = get(f"{API}/orgs/{ORG}/repos?per_page=100&type=public")
    projects = []
    for repo in sorted(repos, key=lambda r: r["pushed_at"], reverse=True):
        if not is_project(repo):
            continue
        print("reading", repo["name"])
        p = read_project(repo)
        p["maker_short"] = (p["maker"] or "").split("·")[0].strip()
        projects.append(p)
    if "--demo" in sys.argv:
        projects += [dict(e, example=True) for e in EXAMPLES]

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "projects").mkdir(parents=True)
    shutil.copytree(ROOT / "assets", OUT / "assets")
    (OUT / "index.html").write_text(hub(projects), encoding="utf-8")
    (OUT / "shrink").mkdir()
    (OUT / "shrink" / "index.html").write_text(shrink_page(), encoding="utf-8")
    (OUT / "guide").mkdir()
    (OUT / "guide" / "index.html").write_text(page(
        "Maker guide · DooM", (ROOT / "content" / "guide.html").read_text(encoding="utf-8"), depth=1,
        description="How to add text, photos, videos and design files to a DooM project."), encoding="utf-8")
    for p in projects:
        if p.get("example"):
            continue
        folder = OUT / "projects" / p["slug"]
        folder.mkdir()
        for pg in p["pages"]:
            render = project_summary if pg["slug"] == "index" else project_doc
            (folder / f"{pg['slug']}.html").write_text(render(p, pg), encoding="utf-8")
        if not (folder / "index.html").exists():
            (folder / "index.html").write_text(project_summary(p, {"slug": "index", "html": ""}), encoding="utf-8")
        # The first version of the hub linked to projects/<slug>.html.
        (OUT / "projects" / f"{p['slug']}.html").write_text(redirect(f"{p['slug']}/index.html"), encoding="utf-8")
    (OUT / "projects.json").write_text(json.dumps(
        [{k: v for k, v in p.items() if k not in ("pages", "log_html")} |
         {"pages": [pg["title"] for pg in p.get("pages", [])]} for p in projects], indent=2), encoding="utf-8")
    print(f"wrote {len(projects)} cards to {OUT}")


if __name__ == "__main__":
    main()
