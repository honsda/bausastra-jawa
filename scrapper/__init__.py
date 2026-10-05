"""Scrapper: add words to the dictionary by scraping multiple sources.

Runs 100% on this device:
  - scrapes external sites (needs internet once per run)
  - saves into local DB (Postgres if `docker compose up -d`, else data/bausastra.db SQLite)
  - served locally by `python -m bausastra.cli serve` on http://127.0.0.1:5000

Usage:
  python -m scrapper.run_all --limit 50
  python -m scrapper.run_all --source kbji --words wonten,kang,wong
  python -m scrapper.run_all --source kamusjawa --words banyu
  python -m scrapper.run_all --source wikipedia --limit 5
  python -m scrapper.run_all --list-sources

Each file in this folder = one source. Add a new source by copying
`_template.py` pattern: implement `scrape(words|limit) -> list[dict]`
then register it in `run_all.SOURCES`.
"""
