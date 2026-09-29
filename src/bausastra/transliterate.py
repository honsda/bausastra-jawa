"""Latin <-> Aksara Jawa transliteration, ported from ../sinau (honsda/sinau).

Algorithm (greedy longest-match, same as sinau/web/src/translator.js):
- consonant + vowel -> base aksara + sandhangan
- consonant with no vowel -> final sign (h/r/ng), base+pangkon mid-word, bare base at end
- standalone vowel -> ha + sandhangan
- punctuation mapping, anything else passes through
Tables live in sinau_lexicon.json (vendored from sinau/lexicon.json).
"""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path

_LEX_PATH = Path(__file__).with_name("sinau_lexicon.json")


@lru_cache(maxsize=1)
def lexicon() -> dict:
    return json.loads(_LEX_PATH.read_text(encoding="utf-8"))


def _sorted_keys(d: dict) -> list[str]:
    return sorted(d.keys(), key=len, reverse=True)


def transliterate(text: str) -> str:
    """Latin Javanese -> Aksara Jawa."""
    if not text:
        return ""
    lex = lexicon()
    consonants = lex["consonants"]
    vowels = lex["vowels"]
    finals = lex["final"]
    puncts = lex.get("punctuations", {})
    pangkon = lex["pangkon"]
    clist = _sorted_keys(consonants)
    vlist = _sorted_keys(vowels)
    plist = _sorted_keys(puncts)

    out: list[str] = []
    lower = text.lower()
    i = 0
    n = len(lower)
    while i < n:
        ch = lower[i]
        if ch.isspace():
            out.append(" ")
            i += 1
            continue
        p = next((k for k in plist if lower.startswith(k, i)), None)
        if p is not None:
            out.append(puncts[p])
            i += len(p)
            continue
        c = next((k for k in clist if lower.startswith(k, i)), None)
        if c is not None:
            nxt = i + len(c)
            v = next((k for k in vlist if lower.startswith(k, nxt)), None)
            if v is not None:
                out.append(consonants[c] + vowels[v])
                i = nxt + len(v)
            elif c in finals:
                out.append(finals[c])
                i = nxt
            elif nxt < n:
                out.append(consonants[c] + pangkon)
                i = nxt
            else:
                out.append(consonants[c])
                i = nxt
        else:
            v = next((k for k in vlist if lower.startswith(k, i)), None)
            if v is not None:
                out.append(consonants["h"] + vowels[v])
                i += len(v)
            else:
                out.append(text[i])
                i += 1
    return "".join(out)


def reverse_transliterate(text: str) -> str:
    """Aksara Jawa -> Latin Javanese (same rules as sinau reverseTransl)."""
    if not text:
        return ""
    lex = lexicon()
    pangkon = lex["pangkon"]
    rev_cons: dict[str, str] = {}
    for k, v in lex["consonants"].items():
        if v not in rev_cons or len(k) < len(rev_cons[v]):
            rev_cons[v] = k
    rev_vow = {v: k for k, v in lex["vowels"].items() if v != ""}
    rev_fin = {v: k for k, v in lex["final"].items()}
    rev_punct = {v: k for k, v in lex.get("punctuations", {}).items()}
    units = sorted(set(rev_cons) | set(rev_vow) | set(rev_fin) | set(rev_punct) | {pangkon},
                   key=len, reverse=True)

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            out.append(" ")
            i += 1
            continue
        u = next((k for k in units if text.startswith(k, i)), None)
        if u is None:
            out.append(ch)
            i += 1
            continue
        if u in rev_cons:
            lat = rev_cons[u]
            nxt = i + len(u)
            v = next((k for k in rev_vow if text.startswith(k, nxt)), None)
            if v is not None:
                out.append(lat + rev_vow[v])
                i = nxt + len(v)
            elif text.startswith(pangkon, nxt):
                out.append(lat)
                i = nxt + len(pangkon)
            else:
                out.append(lat + "a")
                i = nxt
        elif u in rev_vow:
            out.append(rev_vow[u])
            i += len(u)
        elif u in rev_fin:
            out.append(rev_fin[u])
            i += len(u)
        elif u in rev_punct:
            out.append(rev_punct[u])
            i += len(u)
        else:  # bare pangkon
            i += len(u)
    return "".join(out)


def backfill_aksara(engine, langs=("jv", "kawi"), batch: int = 500) -> int:
    """Fill missing aksara_jawa for dictionary entries. Returns rows updated."""
    from sqlalchemy import text as stext
    total = 0
    while True:
        with engine.begin() as conn:
            rows = conn.execute(stext("""
                SELECT id, headword FROM entries
                WHERE (aksara_jawa IS NULL OR aksara_jawa='')
                  AND lang IN ('jv','kawi')
                LIMIT :b
            """), {"b": batch}).all()
            if not rows:
                break
            for eid, hw in rows:
                conn.execute(stext("UPDATE entries SET aksara_jawa=:a WHERE id=:i"),
                             {"a": transliterate(hw or "")[:500], "i": eid})
            total += len(rows)
        print(f"[transliterate] backfilled {total}...")
    print(f"[transliterate] done: {total} rows")
    return total
