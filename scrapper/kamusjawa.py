"""kamusjawa.net source (best-effort HTML scrape, jv <-> id).

URL pattern: https://www.kamusjawa.net/2013/01/<word>.html (site search varies,
so we try direct slug + site search fallback). Check robots/ToS before bulk runs.
"""
from __future__ import annotations
from urllib.parse import quote
from .base import fetch, soup_of, clean

NAME = "kamusjawa-net"
URL = "https://www.kamusjawa.net"
SEARCH = "https://www.kamusjawa.net/search?q="


def _parse_detail(html: str) -> list[dict]:
    soup = soup_of(html)
    items: list[dict] = []
    # article body usually holds "word : meaning"
    for sel in ("div.post-body", "div.entry-content", "article", "main"):
        body = soup.select_one(sel)
        if body:
            text = clean(body.get_text(" ", strip=True))
            if len(text) > 20:
                # take first 1-2 sentences as gloss
                gloss = text[:800]
                return [{"headword": "", "definition": gloss}]
            break
    return items


def scrape(words: list[str] | None = None, limit: int = 50, delay: float = 0.8) -> list[dict]:
    words = (words or ["banyu", "wonten", "kang", "sugeng", "mangan"])[:limit]
    items: list[dict] = []
    for w in words:
        try:
            html = fetch(SEARCH + quote(w), delay=delay)
            soup = soup_of(html)
            link = None
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if "kamusjawa.net" in href and w[:4].lower() in href.lower():
                    link = href
                    break
            if not link:
                continue
            detail = fetch(link, delay=delay)
            for d in _parse_detail(detail):
                d["headword"] = w
                d["lang"] = "jv"
                d["def_lang"] = "id"
                d["source_ref"] = link
                items.append(d)
        except Exception as e:
            print(f"[scrapper:kamusjawa] {w} failed: {e}")
    return items
