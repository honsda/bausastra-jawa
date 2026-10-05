"""Latin <-> Aksara Jawa transliteration, ported from ../sinau (honsda/sinau).

Algorithm (greedy longest-match, same as sinau/web/src/translator.js):
- consonant + vowel -> base aksara + sandhangan
- consonant with no vowel -> final sign (h/r/ng), base+pangkon mid-word, bare base at end
- standalone vowel -> ha + sandhangan
- punctuation mapping, anything else passes through
- hyphens: full reduplication (dalan-dalan, dalan2) -> base + ꧒ (U+A9D2);
  other hyphens are dropped (tutur-kata -> tuturkata). Reverse expands ꧒
  back to word-word.
Tables live in sinau_lexicon.json (vendored from sinau/lexicon.json).
"""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path

REDUPL = "\ua9d2"  # U+A9D2 reduplication marker (dalan-dalan -> dalan + 2-sign)
_SENTINEL = "\ue000"  # private-use placeholder used while reversing
_REDUPL2_RE = re.compile(r"^([A-Za-z]+)2$")

_LEX_PATH = Path(__file__).with_name("sinau_lexicon.json")


@lru_cache(maxsize=1)
def lexicon() -> dict:
    return json.loads(_LEX_PATH.read_text(encoding="utf-8"))


def _sorted_keys(d: dict) -> list[str]:
    return sorted(d.keys(), key=len, reverse=True)


@lru_cache(maxsize=1)
def _tables():
    """Pre-sorted lookup tables + precomputed char sets (built once)."""
    lex = lexicon()
    consonants = lex["consonants"]
    vowels = lex["vowels"]
    finals = lex["final"]
    puncts = lex.get("punctuations", {})
    return {
        "lex": lex,
        "consonants": consonants,
        "vowels": vowels,
        "finals": finals,
        "puncts": puncts,
        "pangkon": lex["pangkon"],
        "clist": _sorted_keys(consonants),
        "vlist": _sorted_keys(vowels),
        "plist": _sorted_keys(puncts),
        "vowel_signs": {v for v in vowels.values() if v},
        "final_signs": set(finals.values()),
        "cons_set": set(consonants.values()),
    }


def transliterate(text: str) -> str:
    """Latin Javanese -> Aksara Jawa.

    Hyphenated tokens: full reduplication (dalan-dalan) becomes base + 2-sign;
    a trailing 2 works too (dalan2); other hyphens are dropped (tutur-kata
    becomes tuturkata).
    """
    if not text:
        return ""
    return "".join(_transl_token(t) for t in re.split(r"(\s+)", text))


def _with_paten(base_aksara: str, latin: str = "") -> str:
    """Kill the final vowel of a base (dalan -> dalan + pangkon).

    Only when the word ends in a consonant *sound*: the Latin form must end
    in a consonant letter (tuwa ends in 'a' -> no paten) AND the aksara must
    end in a bare consonant (vowel/final signs and pangkon stay untouched).
    """
    if not base_aksara:
        return base_aksara
    if latin and latin[-1].lower() in "aiueo":
        return base_aksara
    t = _tables()
    vowel_signs = t["vowel_signs"]
    final_signs = t["final_signs"]
    cons = t["cons_set"]
    last = base_aksara[-1]
    if last in vowel_signs or last in final_signs or last == t["lex"]["pangkon"]:
        return base_aksara
    if last in cons:
        return base_aksara + t["lex"]["pangkon"]
    return base_aksara


def _transl_token(tok: str) -> str:
    if not tok or tok.isspace():
        return tok
    if "-" in tok:
        parts = tok.split("-")
        if (len(parts) >= 2 and parts[0]
                and all(p and p.lower() == parts[0].lower() for p in parts)):
            return _with_paten(_transl_core(parts[0]), parts[0]) + REDUPL
        joined = "".join(parts)
        return _with_paten(_transl_core(joined), joined)
    m = _REDUPL2_RE.fullmatch(tok)
    if m:
        return _with_paten(_transl_core(m.group(1)), m.group(1)) + REDUPL
    return _with_paten(_transl_core(tok), tok)


def _transl_core(text: str) -> str:
    """Greedy longest-match transliteration of hyphen-free text."""
    if not text:
        return ""
    t = _tables()
    consonants = t["consonants"]
    vowels = t["vowels"]
    finals = t["finals"]
    puncts = t["puncts"]
    pangkon = t["pangkon"]
    clist = t["clist"]
    vlist = t["vlist"]
    plist = t["plist"]

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
    """Aksara Jawa -> Latin Javanese (same rules as sinau reverseTransl).

    A trailing 2-sign expands back to reduplication (dalan + 2-sign becomes
    dalan-dalan); a standalone 2-sign becomes "2".
    """
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
    units = sorted(set(rev_cons) | set(rev_vow) | set(rev_fin) | set(rev_punct) | {pangkon, REDUPL},
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
        if u == REDUPL:
            out.append(_SENTINEL)
            i += len(u)
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
    return _expand_redupl("".join(out))


def _expand_redupl(s: str) -> str:
    """Expand 2-sign sentinels: word + sign -> word-word, lone sign -> "2"."""
    parts = []
    for p in re.split(r"(\s+)", s):
        if _SENTINEL in p and not p.isspace():
            while p.endswith(_SENTINEL):
                base = p[:-1]
                m = re.search(r"[A-Za-z]+$", base)
                if not m:
                    break
                p = base + "-" + m.group(0)
            p = p.replace(_SENTINEL, "2")
        parts.append(p)
    return "".join(parts)


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
