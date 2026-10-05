"""Shared helpers for all scrappers: polite HTTP + save to local dictionary DB."""
from __future__ import annotations
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "BausastraBot/0.1 (+javanese-dictionary-research; contact: local)"}


def fetch(url: str, timeout: int = 30, delay: float = 0.4) -> str:
    """GET url, return HTML text. Sleeps `delay` to be polite."""
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    time.sleep(delay)
    return r.text


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def clean(text: str) -> str:
    return " ".join((text or "").split()).strip()


def save_words(engine, source_name: str, source_url: str, items: list[dict]) -> int:
    """Save [{headword, definition, lang?, speech_level?}] into entries/definitions.

    Reuses src/bausastra/bootstrap.py so entries get headword_norm + aksara_jawa
    automatically. Idempotent (ON CONFLICT / OR IGNORE). Returns inserted count.
    """
    from bausastra import bootstrap as bs
    sid = bs.upsert_source(engine, source_name, source_url, kind="dictionary",
                           notes="via scrapper/ multi-source pipeline")
    is_pg = bs.is_postgres(engine)
    batch = [{"headword": clean(d.get("headword", ""))[:200],
              "definition": clean(d.get("definition", ""))[:4000],
              "lang": d.get("lang", "jv"),
              "def_lang": d.get("def_lang", "id"),
              "pos": d.get("pos"),
              "speech_level": d.get("speech_level"),
              "source_id": sid,
              "source_ref": (d.get("source_ref") or "")[:500],
              "context": d.get("context"),
              "raw": {"scrapper": source_name, **(d.get("raw") or {})}}
             for d in items if d.get("headword") and d.get("definition")]
    n = bs.flush_batch(engine, batch)
    print(f"[scrapper:{source_name}] saved {n}/{len(batch)} new entries")
    return n
