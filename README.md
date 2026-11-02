# DooM hub

The front page for the Mars College Department of Makers: https://mars-doom.github.io/

It lists every project repo in the [mars-doom](https://github.com/mars-doom) org
and republishes each project's docs pages inside the hub, one tab per page in the
order of the repo's `mkdocs.yml` nav. Nothing here is edited by hand per project:
the pages are built from each repo's `docs/*.md`, `mkdocs.yml`, `parts.csv` and
`files/` folder.

GitHub renders the markdown. Before that, `build.py` handles the MkDocs parts
GitHub doesn't know: it fills in `{{ parts_list }}` and `{{ files_list }}` the
way `tools/makers.py` does, turns `!!! warning` admonitions into GitHub alerts,
drops `{ width=... }` attributes, and draws ```` ```mermaid ```` diagrams with
mermaid.js. Links between doc pages stay in the hub; links to anything else in
the repo open on GitHub.

## Shrink photos

`/shrink/` is a phone page that shrinks photos before they're uploaded to a
project: pick photos, save the small JPEG copies to the camera roll, upload
those in Pages CMS. It runs entirely in the browser (`assets/shrink.js`), so
nothing is uploaded anywhere, and re-encoding drops the photos' location data.

## How it updates

`.github/workflows/publish-hub.yml` runs `build.py` and publishes `site/` with
GitHub Pages on every push, every night, and on demand from the Actions tab.
A new project shows up on the next run.

## Which repos show up

Every public, non-archived repo in the org except template repos and the
names in `SKIP` at the top of `build.py`.

## What the hub reads from a project

- The first image in `docs/index.md` is the card photo.
- `**Status:**` and `**Maker:**` lines in `docs/index.md` fill the header.
- The first `###` entry in `docs/log.md` is shown as the latest log entry.
- `parts.csv` gives the parts count and, from `qty` × `cost_each`, the cost.

## Run it locally

    python build.py            # writes site/
    python build.py --demo     # plus example cards
    python -m http.server -d site
