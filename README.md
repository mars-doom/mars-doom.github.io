# DooM hub

The front page for the Mars College Department of Makers: https://mars-doom.github.io/

It lists every project repo in the [mars-doom](https://github.com/mars-doom) org
and gives each one a summary page that links to its full docs site. Nothing here
is edited by hand per project: the pages are built from each repo's
`docs/index.md`, `docs/log.md`, `mkdocs.yml` and `parts.csv`.

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
