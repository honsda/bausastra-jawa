"""KBJI source: https://kbji.kemendikdasmen.go.id (Balai Bahasa DIY, ~41.2k lema).

No API key needed. Reuses the proven parser in src/bausastra/bootstrap.py.
Best for accurate jv -> id definitions + speech levels.
"""
from __future__ import annotations
import requests
from bausastra import bootstrap as bs
from .base import HEADERS, clean
import time

BASE = "https://kbji.kemendikdasmen.go.id"
NAME = "kbji-official"
URL = BASE


def scrape(words: list[str] | None = None, limit: int = 50, delay: float = 0.4) -> list[dict]:
    words = words or []
    if not words:
        # default: pull top unknown candidates from crawl data (enrichment loop)
        try:
            from bausastra.db import get_engine
            from sqlalchemy import text as stext
            eng = get_engine()
            with eng.connect() as c:
                rows = c.execute(stext(
                    "SELECT token_norm FROM token_observations WHERE is_known=0 "
                    "GROUP BY token_norm ORDER BY SUM(count) DESC LIMIT :lim"),
                    {"lim": limit}).all()
            words = [r[0] for r in rows if r[0] and len(r[0]) > 2]
        except Exception:
            words = []
    if not words:
        words = ["wonten", "kang", "wong", "banyu", "sugeng"]
    items: list[dict] = []
    for w in words[:limit]:
        try:
            slug = requests.utils.quote(w.strip(), safe="")
            r = requests.get(f"{BASE}/kata/{slug}", headers=HEADERS, timeout=30)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            for head, defi in bs.kbji_parse_search(r.text):
                items.append({"headword": clean(head), "definition": clean(defi),
                              "lang": "jv", "def_lang": "id",
                              "source_ref": f"/kata/{slug}"})
        except Exception as e:
            print(f"[scrapper:kbji] {w} failed: {e}")
        time.sleep(delay)
    return items
