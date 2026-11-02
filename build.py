"""
Builds the DooM hub: a static site that lists every project repo in the
mars-doom GitHub org and gives each one a summary page.

    python build.py            # real repos only
    python build.py --demo     # plus a few example cards, to see a fuller gallery

Output goes to site/. Standard library only. Set GITHUB_TOKEN to lift the API
rate limit from 60 to 5,000 requests an hour (Actions provides one for free).
"""
import csv
import html
import io
import json
import os
import re
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ORG = "mars-doom"
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

# Repos that are never projects, even though they live in the org.
SKIP = {".github", "doom-template", f"{ORG}.github.io"}


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
    return get(f"{RAW}/{ORG}/{repo['name']}/{repo['default_branch']}/{path}", raw=True)


def render_markdown(text, repo):
    """GitHub renders the markdown, so it looks just like the repo does."""
    return get(f"{API}/markdown", {"text": text, "mode": "gfm",
                                   "context": repo["full_name"]}, raw=True)


# ---- Reading a project ----------------------------------------------------

def strip_front_matter(md):
    return re.sub(r"\A---\n.*?\n---\n", "", md or "", flags=re.S)


def fix_links(md, repo):
    """Point relative images at the raw files and relative .md links at the docs site."""
    raw = f"{RAW}/{ORG}/{repo['name']}/{repo['default_branch']}/docs/"
    md = re.sub(r"!\[([^\]]*)\]\((?!https?:)([^)]+)\)", lambda m: f"![{m[1]}]({raw}{m[2]})", md)
    md = re.sub(r"(?<!!)\[([^\]]*)\]\((?!https?:)([^)#]+)\.md\)",
                lambda m: f"[{m[1]}]({docs_url(repo)}{m[2]}/)", md)
    return md


def docs_url(repo):
    return repo["homepage"] or f"https://{ORG}.github.io/{repo['name']}/"


def field(md, name):
    m = re.search(rf"\*\*{name}:\*\*\s*(.+)", md)
    return m[1].strip() if m else None


def read_project(repo):
    index = strip_front_matter(repo_file(repo, "docs/index.md"))
    log = strip_front_matter(repo_file(repo, "docs/log.md"))
    mkdocs = repo_file(repo, "mkdocs.yml") or ""
    parts = repo_file(repo, "parts.csv") or ""

    title = re.search(r"^site_name:\s*(.+)$", mkdocs, re.M)
    h1 = re.search(r"^# (.+)$", index, re.M)
    hero = re.search(r"!\[[^\]]*\]\(([^)]+)\)", index)
    author = re.search(r"^site_author:\s*(.+)$", mkdocs, re.M)

    # The summary page shows the index without what the header already shows.
    body = index
    for pattern in (r"^# .+\n", r"!\[[^\]]*\]\([^)]+\)\n", r"\*\*Status:\*\*.*\n",
                    r"\*\*Maker:\*\*.*\n", r"## Pages\n(?:\n|- .*\n)*"):
        body = re.sub(pattern, "", body, count=1, flags=re.M)

    # Newest log entry: the first ### heading and everything up to the next one.
    entry = re.search(r"^### (.+?)\n(.*?)(?=^### |\Z)", log, re.M | re.S)

    rows = list(csv.DictReader(io.StringIO(parts)))
    cost = sum(float(re.sub(r"[^\d.]", "", r.get("cost_each") or "") or 0) *
               float(r.get("qty") or 1) for r in rows)

    contributors = get(f"{API}/repos/{repo['full_name']}/contributors") or []

    return {
        "slug": repo["name"],
        "name": h1[1].strip() if h1 else (title[1].strip() if title else repo["name"]),
        "description": repo["description"] or "",
        "status": field(index, "Status"),
        "maker": field(index, "Maker") or (author[1].strip() if author else None),
        "hero": f"{RAW}/{ORG}/{repo['name']}/{repo['default_branch']}/docs/{hero[1]}"
                if hero and not hero[1].startswith("http") else (hero[1] if hero else None),
        "summary_html": render_markdown(fix_links(body, repo), repo),
        "log_title": entry[1].strip() if entry else None,
        "log_html": render_markdown(fix_links(entry[2], repo), repo) if entry else None,
        "parts_count": len(rows),
        "parts_cost": cost,
        "contributors": [{"login": c["login"], "avatar": c["avatar_url"], "url": c["html_url"]}
                         for c in contributors if c.get("type") == "User"],
        "updated": repo["pushed_at"][:10],
        "repo_url": repo["html_url"],
        "docs_url": docs_url(repo),
        "issues_url": repo["html_url"] + "/issues",
        "license": (repo.get("license") or {}).get("spdx_id"),
    }


def is_project(repo):
    return (repo["name"] not in SKIP and not repo["name"].startswith(".")
            and not repo["is_template"] and not repo["archived"] and not repo["private"])


# ---- Pages ----------------------------------------------------------------

def page(title, body, depth=0, description=""):
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{up}assets/style.css">
</head>
<body>
<header class="topbar">
  <a class="wordmark" href="{up}index.html">Doo<span>M</span></a>
  <nav>
    <a href="{up}index.html#about">About</a>
    <a href="{up}index.html#projects">Projects</a>
    <a href="{up}index.html#start">Start a project</a>
    <a href="https://github.com/{ORG}">GitHub</a>
  </nav>
</header>
{body}
<footer class="foot">
  <p>Mars College Department of Makers · Hardware CERN-OHL-W-2.0 · Docs and photos CC BY-SA 4.0</p>
  <p class="mono">Built {datetime.now():%Y-%m-%d} from <a href="https://github.com/{ORG}">github.com/{ORG}</a></p>
</footer>
</body>
</html>
"""


def card(p, link):
    img = f'<img src="{html.escape(p["hero"])}" alt="" loading="lazy" decoding="async">' if p.get("hero") else ""
    status = f'<span class="chip">{html.escape(p["status"])}</span>' if p.get("status") else ""
    who = f'<span class="who">{html.escape(p["maker_short"])}</span>' if p.get("maker_short") else ""
    cls = ' class="example"' if p.get("example") else ""
    return f"""<li{cls}><a href="{link}">{img}{status}<span class="label"><span class="name">{html.escape(p["name"])}</span>{who}</span></a></li>"""


def hub(projects):
    cards = "\n".join(card(p, f"projects/{p['slug']}.html" if not p.get("example") else "#projects")
                      for p in projects)
    makers = {c["login"] for p in projects for c in p.get("contributors", [])}
    return page("DooM · Mars College Department of Makers", f"""
<section class="hero">
  <p class="eyebrow mono">Mars College · Department of Makers</p>
  <h1>Build it in the desert.<br>Write it down so anyone can build it again.</h1>
  <p class="lede">DooM is the makers' department at Mars College. Every project here is
  open hardware: a repo with the design, the parts list, the build log and the
  mistakes, published so the next person starts further along.</p>
  <div class="stats mono">
    <span><b>{sum(1 for p in projects if not p.get("example"))}</b> projects</span>
    <span><b>{len(makers)}</b> makers</span>
    <span><b>2027</b> season</span>
  </div>
</section>

<section id="about" class="about">
  <div>
    <h2>What is Mars?</h2>
    <p><span class="todo">[Placeholder copy]</span> Mars College is a seasonal, off-grid
    educational community in the California desert. For a few months each year people
    live, learn and build together with solar power, hauled water and whatever the
    desert allows.</p>
  </div>
  <div>
    <h2>What is DooM?</h2>
    <p>The Department of Makers is where the building happens: shelters, showers, water,
    power, tools. Makers-in-residence each take on a project and document it from the
    first sketch to the finished build, using a shared template so every project reads
    the same way.</p>
  </div>
  <div>
    <h2>Why open?</h2>
    <p>Off-grid problems repeat everywhere. A shower pod that works at Mars should work at
    a farm, a festival or a disaster site. Hardware is CERN-OHL-W, docs are CC BY-SA:
    copy it, change it, share what you learn.</p>
  </div>
</section>

<section id="projects" class="projects">
  <div class="section-head">
    <h2>Projects</h2>
    <p class="mono muted">Pulled live from github.com/{ORG}</p>
  </div>
  <ul class="cards">
{cards}
  </ul>
</section>

<section id="start" class="start">
  <h2>Start a project</h2>
  <p>Every project starts from the DooM template: a ready-made repo with a docs site, a
  parts list, a build log and a printable PDF. Make yours, fill in the pages, and it
  shows up here on the next build.</p>
  <a class="button" href="https://github.com/new?template_owner={ORG}&template_name=doom-template&owner={ORG}">Use the template</a>
  <a class="button ghost" href="https://github.com/{ORG}/doom-template/blob/main/START-HERE.md">Read START-HERE</a>
</section>
""", description="Open hardware projects from the Mars College Department of Makers.")


def project_page(p):
    e = html.escape
    hero = f'<img class="project-hero" src="{e(p["hero"])}" alt="">' if p["hero"] else ""
    people = "".join(f'<a href="{e(c["url"])}" title="{e(c["login"])}"><img src="{e(c["avatar"])}&s=80" alt="{e(c["login"])}"></a>'
                     for c in p["contributors"])
    cost = f"${p['parts_cost']:,.0f}" if p["parts_cost"] else "not costed yet"
    log = f"""
    <section class="panel">
      <h2>Latest from the log</h2>
      <p class="mono muted">{e(p["log_title"])}</p>
      <div class="prose clamp">{p["log_html"]}</div>
      <a href="{e(p["docs_url"])}log/">Read the full log →</a>
    </section>""" if p["log_html"] else ""
    return page(f"{p['name']} · DooM", f"""
<main class="project">
  <p class="crumbs mono"><a href="../index.html#projects">Projects</a> / {e(p["slug"])}</p>
  <div class="project-head">
    <div>
      {f'<span class="chip">{e(p["status"])}</span>' if p["status"] else ""}
      <h1>{e(p["name"])}</h1>
      <p class="lede">{e(p["description"])}</p>
      <p class="muted">{e(p["maker"] or "")}</p>
      <div class="actions">
        <a class="button" href="{e(p["docs_url"])}">Full project docs</a>
        <a class="button ghost" href="{e(p["repo_url"])}">Repository</a>
        <a class="button ghost" href="{e(p["docs_url"])}build-it/">Build it</a>
      </div>
    </div>
    {hero}
  </div>

  <div class="project-body">
    <article class="prose">{p["summary_html"]}</article>
    <aside>
      <section class="panel facts mono">
        <div><span>Updated</span><b>{e(p["updated"])}</b></div>
        <div><span>Parts</span><b>{p["parts_count"]} line items</b></div>
        <div><span>Est. cost</span><b>{cost}</b></div>
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
""", depth=1, description=p["description"])


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
    for p in projects:
        if not p.get("example"):
            (OUT / "projects" / f"{p['slug']}.html").write_text(project_page(p), encoding="utf-8")
    (OUT / "projects.json").write_text(json.dumps(
        [{k: v for k, v in p.items() if not k.endswith("_html")} for p in projects], indent=2), encoding="utf-8")
    print(f"wrote {len(projects)} cards to {OUT}")


if __name__ == "__main__":
    main()
