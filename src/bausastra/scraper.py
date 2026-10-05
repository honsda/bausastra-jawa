"""Online scraping tool: fetch Javanese sites, extract text, identify known/unknown words.

Usage (CLI):
    python -m bausastra.cli scrape --url https://jv.wikipedia.org/wiki/Basa_Jawa
    python -m bausastra.cli scrape --file urls.txt --limit 20
    python -m bausastra.cli identify --text "sugeng rawuh, piye kabare?"
"""
from __future__ import annotations
import logging
from collections import Counter
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

from .javanese import tokenize, normalize, javanese_score, guess_lang

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "BausastraBot/0.1 (+javanese-dictionary-research; contact: local)"}
MAX_TEXT_CHARS = 20000
MAX_TOKENS_SAVED = 5000


def fetch_html(url: str, timeout: int = 20, retries: int = 2) -> tuple[str, str]:
    """Return (title, visible_text). Uses trafilatura if available, else BeautifulSoup."""
    last_err: Exception | None = None
    r = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            break
        except Exception as e:
            last_err = e
            if attempt >= retries:
                raise
            log.warning("fetch %s attempt %d failed: %s", url, attempt + 1, e)
    assert r is not None
    html = r.text
    # Try trafilatura for clean article text
    text = ""
    try:
        import trafilatura
        dl = trafilatura.extract(html, include_comments=False, include_tables=False)
        if dl:
            text = dl
    except Exception:
        pass
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else url
    if not text:
        for tag in soup(["script", "style", "nav", "footer", "header", "form"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
    return title, text


def analyze_text(text: str, dictionary: set[str] | None = None) -> dict:
    """Identify words: coverage vs dictionary set (normalized forms)."""
    text = (text or "")[:100000]  # cap CPU on huge pages
    tokens = tokenize(text)
    norms = [normalize(t) for t in tokens]
    counts = Counter(norms)
    score, stats = javanese_score(text)
    known, unknown = {}, {}
    if dictionary is not None:
        for tok, c in counts.items():
            (known if tok in dictionary else unknown)[tok] = c
    # cap persisted token map to avoid DB explosion on long crawls
    top_counts = dict(counts.most_common(MAX_TOKENS_SAVED))
    return {
        "word_count": len(tokens),
        "unique_words": len(counts),
        "freq": counts.most_common(50),
        "javanese_score": score,
        "lang_guess": guess_lang(text),
        "lang_stats": stats,
        "known_words": len(known),
        "unknown_words": len(unknown),
        "known": dict(sorted(known.items(), key=lambda x: -x[1])[:50]),
        "unknown": dict(sorted(unknown.items(), key=lambda x: -x[1])[:50]),
        "all_counts": top_counts,
    }


def scrape_url(url: str, dictionary: set[str] | None = None) -> dict:
    title, text = fetch_html(url)
    analysis = analyze_text(text, dictionary)
    return {
        "url": url,
        "domain": urlparse(url).netloc,
        "title": title,
        "raw_text": text[:MAX_TEXT_CHARS],  # truncate for DB
        **analysis,
    }


def load_dictionary_words(engine) -> set[str]:
    """Load normalized headwords from DB for matching."""
    from sqlalchemy import text as stext
    try:
        with engine.connect() as c:
            rows = c.execute(stext("SELECT headword_norm FROM entries")).all()
        return {r[0] for r in rows if r[0]}
    except Exception:
        return set()
