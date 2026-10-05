"""Orchestrator: run one or all sources, save to local DB (this device's server).

Examples (run from repo root, venv active):
  python -m scrapper.run_all --list-sources
  python -m scrapper.run_all --source kbji --words wonten,kang,wong
  python -m scrapper.run_all --source kamusjawa --words banyu,sugeng
  python -m scrapper.run_all --source wikipedia --limit 5
  python -m scrapper.run_all --all --limit 30        # all sources, then serve
  python -m scrapper.run_all --all --no-save         # dry-run, print only

After scraping, view words locally:
  python -m bausastra.cli lookup --q <word>
  python -m bausastra.cli serve --port 5000   # http://127.0.0.1:5000
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bausastra.db import get_engine  # noqa: E402
from .base import save_words  # noqa: E402
from . import kbji, kamusjawa, wikipedia_jv, ai_enrich  # noqa: E402

SOURCES = {
    "kbji": {"mod": kbji, "desc": "KBJI official (accurate jv->id, recommended)"},
    "kamusjawa": {"mod": kamusjawa, "desc": "kamusjawa.net (best-effort)"},
    "wikipedia": {"mod": wikipedia_jv, "desc": "jv.wikipedia (new-word candidates)"},
    "ai": {"mod": ai_enrich, "desc": "local Ollama qwen2.5:3b (AI drafts, needs review)"},
}


def run_one(name: str, words: list[str] | None, limit: int, save: bool) -> int:
    mod = SOURCES[name]["mod"]
    print(f"\n== source: {name} ({mod.NAME}) ==")
    engine = get_engine() if save or hasattr(mod, "run_and_save") else None
    if hasattr(mod, "run_and_save"):
        # AI source: drafts senses + examples; honors --no-save via dry run
        if not save:
            items = mod.scrape(words=words, limit=limit)
            print(f"scraped {len(items)} raw definition rows (dry-run, nothing written)")
            return 0
        return mod.run_and_save(engine, words, limit)
    items = mod.scrape(words=words, limit=limit)
    print(f"scraped {len(items)} raw items")
    for it in items[:10]:
        print(f"  - {it.get('headword')} : {str(it.get('definition'))[:100]}")
    if len(items) > 10:
        print(f"  ... +{len(items) - 10} more")
    if not save:
        print("(dry-run, --no-save: nothing written to DB)")
        return 0
    engine = engine or get_engine()  # local Postgres or SQLite fallback — this device
    return save_words(engine, mod.NAME, mod.URL, items)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Bausastra multi-source scrapper (local server)")
    p.add_argument("--source", choices=list(SOURCES), help="single source to run")
    p.add_argument("--all", action="store_true", help="run all sources")
    p.add_argument("--list-sources", action="store_true", help="list sources and exit")
    p.add_argument("--words", default="", help="comma-separated words/URLs, e.g. wonten,kang")
    p.add_argument("--from-file", default="", help="file with words/URLs, one per line")
    p.add_argument("--limit", type=int, default=30, help="max words/pages per source")
    p.add_argument("--no-save", action="store_true", help="dry-run, don't write DB")
    a = p.parse_args(argv)

    if a.list_sources:
        for k, v in SOURCES.items():
            print(f"{k:12} {v['mod'].NAME:20} {v['desc']}  ({v['mod'].URL})")
        return 0

    words: list[str] = [w.strip() for w in a.words.split(",") if w.strip()]
    if a.from_file:
        words += [l.strip() for l in Path(a.from_file).read_text(encoding="utf-8").splitlines()
                  if l.strip() and not l.strip().startswith("#")]

    targets = list(SOURCES) if a.all else ([a.source] if a.source else [])
    if not targets:
        p.error("provide --source NAME or --all (or --list-sources)")
    total = 0
    for t in targets:
        total += run_one(t, words or None, a.limit, save=not a.no_save)
    print(f"\nDone. new entries saved: {total} (local DB, view via `serve --port 5000`)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
