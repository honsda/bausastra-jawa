"""Javanese Wikipedia source: discover NEW candidate words from real Javanese text.

Flow: fetch jv.wikipedia articles -> tokenize -> compare vs local entries ->
return unknown tokens as headword candidates (definition = example sentence).
These get real glosses later via `kbji --unknown-top N`.
"""
from __future__ import annotations
from bausastra.scraper import fetch_html
from bausastra.javanese import tokenize, normalize
from .base import clean

NAME = "jv-wikipedia"
URL = "https://jv.wikipedia.org"
SEEDS = [
    "https://jv.wikipedia.org/wiki/Basa_Jawa",
    "https://jv.wikipedia.org/wiki/Sastra_Jawa",
    "https://jv.wikipedia.org/wiki/Wayang",
    "https://jv.wikipedia.org/wiki/Gamelan",
    "https://jv.wikipedia.org/wiki/Surakarta",
]


def scrape(words: list[str] | None = None, limit: int = 5, delay: float = 0.4) -> list[dict]:
    urls = words if words and words[0].startswith("http") else SEEDS[:limit]
    try:
        from bausastra.db import get_engine
        from bausastra.scraper import load_dictionary_words
        known = load_dictionary_words(get_engine())
    except Exception:
        known = set()
    items: list[dict] = []
    seen: set[str] = set()
    for url in urls:
        try:
            title, text = fetch_html(url)
        except Exception as e:
            print(f"[scrapper:wikipedia] {url} failed: {e}")
            continue
        for tok in tokenize(text):
            norm = normalize(tok)
            if len(norm) < 3 or norm in known or norm in seen:
                continue
            if not norm.isalpha() or not norm.isascii():
                continue  # skip Aksara script / numbers, keep Latin headwords only
            seen.add(norm)
            # first sentence containing the token as context gloss
            ctx = next((s.strip() for s in text.split(".") if tok.lower() in s.lower()), "")[:400]
            items.append({"headword": norm, "definition": f"candidate from {title}: {clean(ctx)}",
                          "lang": "jv", "def_lang": "id", "source_ref": url,
                          "raw": {"discovered_in": title}})
            if len(items) >= limit * 20:
                break
    return items
