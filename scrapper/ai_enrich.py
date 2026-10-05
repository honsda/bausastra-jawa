"""AI source: draft definitions with local Ollama (llama3.2:3b) for unknown words.

Pipeline: broad unknown tokens from crawl data -> Ollama proposes
numbered senses (Indonesian + English kept SEPARATE) + example sentence
-> saved as [AI-DRAFT, needs review] definitions. Glosses go to
definitions (lang=id / lang=en, numbered "1. ...; 2. ..."), usage
examples go to the examples table -- never mashed into one string.

Never auto-publishes as verified: every definition is prefixed
`[AI-DRAFT, needs review]` and context JSON carries
`{"needs_review": true, "ai_model": ...}` so you can filter/approve later.

Requires: Ollama running on http://localhost:11434 + model pulled:
  ollama pull llama3.2:3b
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path
import requests

NAME = "ollama-llama3.2:3b"
URL = "http://localhost:11434"
MODEL = "llama3.2:3b"
API = "http://localhost:11434/api/generate"

# Per-word cooldown so the worker doesn't re-draft the same words daily.
SEEN_FILE = Path(__file__).resolve().parent / "ai_seen.json"
COOLDOWN_DAYS = 7

PROMPT = """Artikan kata bahasa Jawa "{word}" ke bahasa Indonesia dan Inggris.
Konteks (kalimat asli tempat kata ini muncul): "{context}"
Tulis SEMUA arti yang kamu tahu (1-4 arti, paling umum dulu).
Jawab HANYA dengan satu objek JSON valid, tanpa teks lain:
{{"senses": [{{"id": "<arti Indonesia>", "en": "<English gloss, or empty>", "speech_level": "<ngoko|krama_madya|krama_inggil|unknown>", "pos": "<n|v|adj|adv|unknown>", "confidence": <0.0-1.0>}}],
"example_jv": "<satu contoh kalimat Jawa memakai kata ini>",
"example_id": "<terjemahan Indonesia contoh itu>"}}
Contoh untuk kata "mangan": {{"senses": [{{"id": "makan", "en": "to eat", "speech_level": "ngoko", "pos": "v", "confidence": 0.9}}], "example_jv": "Aku mangan sega.", "example_id": "Saya makan nasi."}}
JSON:"""

# Function words / ref debris: never draft these.
STOP = frozenset("""
the and for with from that this with yang dan dari dengan untuk pada adalah
ini itu tidak ada juga sudah saya kamu dia mereka kita kami apa siapa mana
bagaimana mengapa karena sing kang ing lan karo saka marang kanggo neng ono
ana ora dudu durung wis arep yen nek banjur nanging opo piye kok yo ya
""".split())


def _ask(word: str, context: str, timeout: int = 180) -> dict:
    r = requests.post(API, json={"model": MODEL,
                                 "prompt": PROMPT.format(word=word, context=context[:500]),
                                 "stream": False,
                                 "options": {"temperature": 0.3, "num_predict": 450}},
                      timeout=timeout)
    r.raise_for_status()
    return r.json()


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _example_for(word: str) -> str:
    """One real sentence containing `word` from crawled Javanese pages (or '')."""
    try:
        from bausastra.db import get_engine
        from sqlalchemy import text as stext
        eng = get_engine()
        with eng.connect() as c:
            rows = c.execute(stext(
                "SELECT raw_text FROM crawl_pages WHERE raw_text LIKE :like LIMIT 3"),
                {"like": f"%{word}%"}).all()
        for (txt,) in rows:
            for s in (txt or "").split("."):
                if word.lower() in s.lower() and 10 < len(s.strip()) < 400:
                    return " ".join(s.strip().split())
    except Exception:
        pass
    return ""


def _clean_senses(data: dict, word: str) -> list[dict]:
    senses = data.get("senses") or []
    # backwards compat: single-gloss answers from older prompts
    if not senses and data.get("definition_id"):
        senses = [{"id": data.get("definition_id"), "en": "",
                   "speech_level": data.get("speech_level"),
                   "pos": data.get("pos"),
                   "confidence": data.get("confidence", 0)}]
    out = []
    for s in senses[:4]:
        gloss = (s.get("id") or "").strip()
        if not gloss:
            continue
        # drop echoes: model just repeating the headword is not a definition
        low = gloss.lower().strip(" .")
        if low == word.lower() or low == f"kata {word.lower()}":
            continue
        try:
            conf = float(s.get("confidence", 0))
        except Exception:
            conf = 0
        level = (s.get("speech_level") or "unknown").strip().lower()
        pos = (s.get("pos") or None)
        out.append({"id": gloss[:500], "en": (s.get("en") or "").strip()[:500],
                    "speech_level": level if level in (
                        "ngoko", "krama_madya", "krama_inggil") else None,
                    "pos": pos if pos in ("n", "v", "adj", "adv") else None,
                    "confidence": conf})
    return out


def draft_one(word: str) -> dict | None:
    """Draft senses for `word`. Returns bundle dict or None.

    Bundle: {"definitions": [save_words items], "example": {...}|None}.
    Indonesian and English glosses are separate definition rows
    (lang=id / lang=en); the example goes to the examples table.
    """
    context = _example_for(word)
    try:
        resp = _ask(word, context)
        data = _parse(resp.get("response", ""))
    except Exception as e:
        print(f"[scrapper:ai] {word} LLM failed: {e}")
        return None
    senses = _clean_senses(data, word)
    if not senses:
        print(f"[scrapper:ai] {word} skipped (no usable senses from model)")
        return None
    level = next((s["speech_level"] for s in senses if s["speech_level"]), None)
    pos = next((s["pos"] for s in senses if s["pos"]), None)
    conf = max(s["confidence"] for s in senses)
    numbered_id = "; ".join(f"{i}. {s['id']}" for i, s in enumerate(senses, 1))
    defs = [{"headword": word,
             "definition": f"[AI-DRAFT, needs review] {numbered_id}"[:4000],
             "lang": "jv", "def_lang": "id",
             "speech_level": level, "pos": pos,
             "source_ref": f"ollama:{MODEL}:{word}",
             "context": json.dumps({"ai_model": MODEL, "needs_review": True,
                                    "confidence": conf, "senses": len(senses)}),
             "raw": {"ai_model": MODEL, "needs_review": True,
                     "confidence": conf, "senses": len(senses)}}]
    en_parts = [(i, s["en"]) for i, s in enumerate(senses, 1) if s["en"]]
    if en_parts:
        numbered_en = "; ".join(f"{i}. {g}" for i, g in en_parts)
        defs.append({"headword": word,
                     "definition": f"[AI-DRAFT, needs review] {numbered_en}"[:4000],
                     "lang": "jv", "def_lang": "en",
                     "speech_level": level, "pos": pos,
                     "source_ref": f"ollama:{MODEL}:{word}:en",
                     "context": json.dumps({"ai_model": MODEL, "needs_review": True,
                                            "confidence": conf}),
                     "raw": {"ai_model": MODEL, "needs_review": True,
                             "confidence": conf}})
    ex_jv = (data.get("example_jv") or "").strip()[:500]
    ex_id = (data.get("example_id") or "").strip()[:500]
    example = ({"headword": word, "javanese_text": ex_jv,
                "translation_text": ex_id or None,
                "source_url": f"ollama:{MODEL}"} if ex_jv else None)
    return {"definitions": defs, "example": example}


def _load_seen() -> dict:
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _mark_seen(words: list[str]) -> None:
    try:
        seen = _load_seen()
        now = time.time()
        seen.update({w: now for w in words})
        # prune entries older than cooldown to keep file small
        cutoff = now - COOLDOWN_DAYS * 86400
        SEEN_FILE.write_text(json.dumps({w: t for w, t in seen.items()
                                         if t > cutoff}),
                             encoding="utf-8")
    except Exception:
        pass


def _in_cooldown(word: str, seen: dict) -> bool:
    return time.time() - seen.get(word, 0) < COOLDOWN_DAYS * 86400


def _ai_drafted_norms() -> set[str]:
    """Headword norms that already have a pending AI draft (skip to avoid repeats)."""
    try:
        from bausastra.db import get_engine
        from sqlalchemy import text as stext
        eng = get_engine()
        with eng.connect() as c:
            rows = c.execute(stext(
                "SELECT DISTINCT e.headword_norm FROM entries e "
                "JOIN definitions d ON d.entry_id=e.id "
                "WHERE d.definition LIKE '[AI-DRAFT%'")).all()
        return {r[0] for r in rows if r[0]}
    except Exception:
        return set()


def _unknown_targets(limit: int) -> list[str]:
    """Broad words first: seen across MANY pages, frequent, not drafted recently."""
    try:
        from bausastra.db import get_engine
        from sqlalchemy import text as stext
        eng = get_engine()
        with eng.connect() as c:
            rows = c.execute(stext(
                "SELECT token_norm, COUNT(DISTINCT page_id) AS pages, SUM(count) AS total "
                "FROM token_observations WHERE is_known=0 "
                "GROUP BY token_norm ORDER BY pages DESC, total DESC LIMIT :lim"),
                {"lim": limit * 10}).all()
        drafted = _ai_drafted_norms()
        seen = _load_seen()
        out = []
        for norm, _pages, _total in rows:
            if (not norm or len(norm) < 4 or not norm.isascii()
                    or not norm.isalpha() or norm.lower() in STOP
                    or norm in drafted or _in_cooldown(norm, seen)):
                continue
            out.append(norm)
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        print(f"[scrapper:ai] unknown-targets fallback ({e})")
        return []


def scrape(words: list[str] | None = None, limit: int = 5, delay: float = 0.2,
           min_conf: float = 0.0) -> list[dict]:
    """Draft definition rows (flat list, save_words-compatible).

    Examples are stashed on the function for run_and_save(); the plain
    run_all path saves definitions (examples need run_and_save).
    """
    words = [w.strip() for w in (words or []) if w.strip()]
    if not words:
        words = _unknown_targets(limit)
    if not words:
        words = ["pamerangan", "lumrahe", "kalebet", "sumebar", "banyuwangi"][:limit]
    items: list[dict] = []
    scrape.pending_examples = []
    for w in words[:limit]:
        print(f"[scrapper:ai] drafting: {w} ...")
        time.sleep(delay)
        bundle = draft_one(w)
        if not bundle:
            continue
        conf = max((d.get("raw") or {}).get("confidence", 0)
                   for d in bundle["definitions"])
        if conf < min_conf:
            print(f"  -> skipped (confidence {conf} < {min_conf})")
            continue
        items.extend(bundle["definitions"])
        if bundle["example"]:
            scrape.pending_examples.append(bundle["example"])
        print(f"  -> {bundle['definitions'][0]['definition'][:120]}")
    _mark_seen(words[:limit])
    return items


scrape.pending_examples: list[dict] = []


def _save_examples(engine, examples: list[dict]) -> int:
    """Insert usage examples into the examples table (deduped per entry)."""
    from sqlalchemy import text as stext
    n = 0
    with engine.begin() as c:
        for ex in examples:
            eid = c.execute(stext(
                "SELECT id FROM entries WHERE headword=:h AND lang='jv' LIMIT 1"),
                {"h": ex["headword"]}).first()
            if not eid:
                continue
            eid = int(eid[0])
            dup = c.execute(stext(
                "SELECT 1 FROM examples WHERE entry_id=:e AND javanese_text=:t LIMIT 1"),
                {"e": eid, "t": ex["javanese_text"]}).first()
            if dup:
                continue
            c.execute(stext(
                "INSERT INTO examples (entry_id, javanese_text, translation_text, "
                "translation_lang, source_url) VALUES (:e,:j,:t,'id',:u)"),
                {"e": eid, "j": ex["javanese_text"],
                 "t": ex.get("translation_text"), "u": ex.get("source_url")})
            n += 1
    return n


def run_and_save(engine, words: list[str] | None, limit: int = 5) -> int:
    """Full AI round: draft senses + glosses + examples, save all. Returns def count."""
    from .base import save_words
    items = scrape(words=words, limit=limit)
    if not items:
        print("[scrapper:ai] nothing drafted this round")
        return 0
    n = save_words(engine, NAME, URL, items)
    m = _save_examples(engine, getattr(scrape, "pending_examples", []))
    print(f"[scrapper:ai] saved {n} definitions + {m} examples")
    return n
