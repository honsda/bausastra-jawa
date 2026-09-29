"""Javanese language helpers: normalization, scoring, tokenization."""
from __future__ import annotations
import re
import unicodedata

# Common Javanese function words — high signal for lang detection.
# Sources: general knowledge of Basa Jawa (ngoko/krama particles).
JAVANESE_STOPWORDS = frozenset("""
aku kowe sampeyan panjenengan dheweke deweke
iki kuwi kae sing kang ing ingkang lan karo soko saka marang menyang
kanggo kanggo neng nang ono ana ora dudu durung wis wes uwis arep bakal
yen nek lamun nalika naliko banjur terus nanging ananging nanging
opo apa piye kepiye kepriye pira sepira endi nendi kok to yo ya wah lah
matur nuwun sugeng rawuh monggo nyuwun tulung dalem kulo kawula mboten
boten nggih injih inggih pripun dospundi sae awon ageng alit gede cilik
basa jawa krama ngoko madya inggil tembung tembung tegese artine
""".split())

INDONESIAN_STOPWORDS = frozenset("""
yang dan di ke dari dengan untuk pada adalah ini itu tidak ada juga sudah
saya kamu dia mereka kita kami apa siapa mana bagaimana mengapa karena
""".split())

_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
_JAVANESE_SCRIPT_RE = re.compile(r"[\uA980-\uA9DF]")  # ꦀ-꧟


def normalize(word: str) -> str:
    w = word.strip().lower()
    w = unicodedata.normalize("NFKD", w)
    w = "".join(c for c in w if not unicodedata.combining(c))
    return w


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 or t in ("a", "i", "o")]


def has_javanese_script(text: str) -> bool:
    return bool(_JAVANESE_SCRIPT_RE.search(text))


def javanese_score(text: str) -> tuple[float, dict]:
    """Return 0..1 score that text is Javanese + debug stats."""
    tokens = tokenize(text)
    if not tokens:
        return 0.0, {"tokens": 0}
    total = len(tokens)
    jv = sum(1 for t in tokens if t in JAVANESE_STOPWORDS)
    idx = sum(1 for t in tokens if t in INDONESIAN_STOPWORDS)
    script_boost = 0.3 if has_javanese_script(text) else 0.0
    # ratio of jv markers minus indonesian markers, normalized
    score = (jv * 2 - idx) / max(total, 1) + script_boost
    score = max(0.0, min(1.0, score * 3))  # scale up; stopwords are sparse
    return round(score, 4), {"tokens": total, "jv_markers": jv, "id_markers": idx,
                             "script": has_javanese_script(text)}


def guess_lang(text: str) -> str:
    score, _ = javanese_score(text)
    if score >= 0.5:
        return "jv"
    if score >= 0.2:
        return "mixed"
    # mostly Indonesian?
    tokens = tokenize(text)
    id_hits = sum(1 for t in tokens if t in INDONESIAN_STOPWORDS)
    if id_hits >= 3:
        return "id"
    return "other"
