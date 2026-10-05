"""Public-use layer: API keys, community submissions, audit log.

Read API is open. Writes work in two tiers:
- anonymous: heavily rate-limited community submits (honeypot + caps)
- API key: higher limits, identified actor for audit

Approved flows reuse review.py: submits land as [COMMUNITY-DRAFT] definitions
so the existing /review desk approves them with one click.
"""
from __future__ import annotations
import hashlib
import json
import logging
import secrets
import time
from sqlalchemy import text as stext
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

COMMUNITY_PREFIX = "[COMMUNITY-DRAFT"
VALID_LEVELS = {"ngoko", "krama_madya", "krama_inggil", "krama_alus", None}
VALID_POS = {"n", "v", "adj", "adv", "pron", None}
VALID_LANGS = {"jv", "kawi", "id"}


def ensure_tables(engine: Engine) -> None:
    """Create api_keys + audit_log if missing (both PG and SQLite)."""
    pg = engine.dialect.name == "postgresql"
    with engine.begin() as c:
        if pg:
            c.execute(stext("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    prefix TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    last_used_at TIMESTAMPTZ,
                    revoked BOOLEAN DEFAULT FALSE
                )
            """))
            c.execute(stext("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id SERIAL PRIMARY KEY,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL DEFAULT '',
                    target_id TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """))
        else:
            c.execute(stext("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    prefix TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    last_used_at TEXT,
                    revoked INTEGER DEFAULT 0
                )
            """))
            c.execute(stext("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL DEFAULT '',
                    target_id TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """))


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_key(engine: Engine, name: str) -> dict:
    """Mint a key. Returns {raw_key (show once), prefix, id}. Only hash is stored."""
    ensure_tables(engine)
    raw = "baus_" + secrets.token_urlsafe(32)
    prefix = raw[:12]
    with engine.begin() as c:
        row = c.execute(stext(
            "INSERT INTO api_keys (name, key_hash, prefix) VALUES (:n,:h,:p) "
            + ("RETURNING id" if engine.dialect.name == "postgresql" else "")),
            {"n": name[:200], "h": _hash(raw), "p": prefix})
        try:
            new_id = row.scalar() if engine.dialect.name == "postgresql" else row.lastrowid
        except Exception:
            new_id = None
        if new_id is None:
            new_id = c.execute(stext("SELECT id FROM api_keys WHERE key_hash=:h"),
                               {"h": _hash(raw)}).scalar()
    audit(engine, f"key:{name}", "apikey.create", "api_key", str(new_id or prefix),
          json.dumps({"prefix": prefix}))
    return {"raw_key": raw, "prefix": prefix, "id": int(new_id or 0)}


def verify_key(engine: Engine, raw: str) -> dict | None:
    """Validate raw key -> {id, name, prefix} or None. Touches last_used_at."""
    if not raw or not raw.startswith("baus_"):
        return None
    ensure_tables(engine)
    with engine.begin() as c:
        row = c.execute(stext(
            "SELECT id, name, prefix FROM api_keys WHERE key_hash=:h AND "
            + ("revoked=FALSE" if engine.dialect.name == "postgresql" else "revoked=0")),
            {"h": _hash(raw)}).first()
        if not row:
            return None
        kid, name, prefix = int(row[0]), row[1], row[2]
        try:
            if engine.dialect.name == "postgresql":
                c.execute(stext("UPDATE api_keys SET last_used_at=now() WHERE id=:i"), {"i": kid})
            else:
                c.execute(stext("UPDATE api_keys SET last_used_at=datetime('now') WHERE id=:i"),
                          {"i": kid})
        except Exception:
            pass
    return {"id": kid, "name": name, "prefix": prefix}


def list_keys(engine: Engine) -> list[dict]:
    ensure_tables(engine)
    with engine.connect() as c:
        rows = c.execute(stext(
            "SELECT id, name, prefix, created_at, last_used_at, revoked FROM api_keys ORDER BY id")).all()
    out = []
    for r in rows:
        rev = r[5]
        out.append({"id": int(r[0]), "name": r[1], "prefix": r[2],
                    "created_at": str(r[3]), "last_used_at": str(r[4] or ""),
                    "revoked": bool(rev)})
    return out


def revoke_key(engine: Engine, ident: str) -> bool:
    """Revoke by id or prefix. Returns True if something changed."""
    ensure_tables(engine)
    with engine.begin() as c:
        try:
            iid = int(ident)
            res = c.execute(stext("UPDATE api_keys SET revoked=TRUE WHERE id=:i")
                            if engine.dialect.name == "postgresql" else
                            stext("UPDATE api_keys SET revoked=1 WHERE id=:i"), {"i": iid})
        except ValueError:
            res = c.execute(stext("UPDATE api_keys SET revoked=TRUE WHERE prefix=:p")
                            if engine.dialect.name == "postgresql" else
                            stext("UPDATE api_keys SET revoked=1 WHERE prefix=:p"), {"p": ident})
        return (res.rowcount or 0) > 0


def audit(engine: Engine, actor: str, action: str, target_type: str = "",
          target_id: str = "", detail: str = "") -> None:
    try:
        ensure_tables(engine)
        with engine.begin() as c:
            c.execute(stext(
                "INSERT INTO audit_log (actor, action, target_type, target_id, detail)"
                " VALUES (:a,:ac,:t,:i,:d)"),
                {"a": (actor or "")[:200], "ac": action[:100],
                 "t": (target_type or "")[:100], "i": (target_id or "")[:200],
                 "d": (detail or "")[:4000]})
    except Exception as e:
        log.warning("audit failed (%s/%s): %s", actor, action, e)


def validate_submit(body: dict) -> tuple[dict | None, str | None]:
    """Validate public submit payload. Returns (clean, error)."""
    if not isinstance(body, dict):
        return None, "invalid JSON body"
    # honeypot: bots fill it, humans never see it
    if (body.get("website") or body.get("url") or "") and str(body.get("website") or body.get("url")).strip():
        return None, "spam detected"
    hw = (body.get("headword") or "").strip()[:200]
    defi = (body.get("definition") or "").strip()[:4000]
    if len(hw) < 2:
        return None, "headword must be >= 2 chars"
    if len(defi) < 2:
        return None, "definition must be >= 2 chars"
    lang = (body.get("lang") or "jv").strip().lower()
    if lang not in VALID_LANGS:
        return None, "lang must be one of jv/kawi/id"
    def_lang = (body.get("def_lang") or "id").strip().lower()
    if def_lang not in ("id", "en", "jv"):
        return None, "def_lang must be one of id/en/jv"
    level = body.get("speech_level") or None
    if isinstance(level, str):
        level = level.strip().lower() or None
    if level not in VALID_LEVELS:
        return None, "speech_level must be ngoko/krama_madya/krama_inggil/krama_alus"
    pos = body.get("pos") or None
    if isinstance(pos, str):
        pos = pos.strip().lower() or None
    if pos not in VALID_POS:
        return None, "pos must be n/v/adj/adv/pron"
    ex_jv = (body.get("example_jv") or "").strip()[:500]
    ex_id = (body.get("example_id") or "").strip()[:500]
    submitter = (body.get("submitter") or body.get("name") or "").strip()[:200]
    return {"headword": hw, "definition": defi, "lang": lang, "def_lang": def_lang,
            "speech_level": level, "pos": pos, "example_jv": ex_jv,
            "example_id": ex_id, "submitter": submitter}, None


def submit_word(engine: Engine, clean: dict, actor: str) -> dict:
    """Insert community draft (entry + [COMMUNITY-DRAFT] definition + optional example)."""
    from .bootstrap import upsert_source, _insert_fast, _is_pg
    ensure_tables(engine)
    sid = upsert_source(engine, "community-submissions", "",
                        kind="manual", language_pair="jv->id",
                        notes="public submissions, needs review")
    is_pg = _is_pg(engine)
    prefix = COMMUNITY_PREFIX + ", needs review] "
    with engine.begin() as conn:
        eid = _insert_fast(conn, is_pg, clean["headword"], prefix + clean["definition"],
                           lang=clean["lang"], def_lang=clean["def_lang"],
                           pos=clean["pos"], speech_level=clean["speech_level"],
                           source_id=sid, source_ref=f"community:{actor[:120]}",
                           context=json.dumps({"needs_review": True, "community": True,
                                               "submitter": clean["submitter"]}),
                           raw={"community": True, "needs_review": True,
                                "submitter": clean["submitter"]})
        if eid is None:
            # entry existed (ON CONFLICT DO NOTHING) — attach draft definition anyway
            row = conn.execute(stext(
                "SELECT id FROM entries WHERE headword=:h AND lang=:l"),
                {"h": clean["headword"], "l": clean["lang"]}).first()
            if not row:
                return {"ok": False, "error": "could not save (duplicate?)"}
            eid = int(row[0])
            conn.execute(stext(
                "INSERT INTO definitions (entry_id, lang, definition, context) VALUES (:e,:l,:d,:c)"),
                {"e": eid, "l": clean["def_lang"], "d": (prefix + clean["definition"])[:4000],
                 "c": json.dumps({"needs_review": True, "community": True})})
        did = conn.execute(stext(
            "SELECT MAX(id) FROM definitions WHERE entry_id=:e"), {"e": eid}).scalar()
        if clean.get("example_jv"):
            dup = conn.execute(stext(
                "SELECT 1 FROM examples WHERE entry_id=:e AND javanese_text=:t LIMIT 1"),
                {"e": eid, "t": clean["example_jv"]}).first()
            if not dup:
                conn.execute(stext(
                    "INSERT INTO examples (entry_id, javanese_text, translation_text,"
                    " translation_lang, source_url) VALUES (:e,:j,:t,'id',:u)"),
                    {"e": eid, "j": clean["example_jv"],
                     "t": clean.get("example_id") or None,
                     "u": f"community:{actor[:120]}"})
    audit(engine, actor, "community.submit", "definition", str(did or ""),
          json.dumps({"headword": clean["headword"], "entry_id": eid}))
    return {"ok": True, "entry_id": eid, "def_id": int(did or 0)}


def report_wrong(engine: Engine, entry_id: int, reason: str, actor: str) -> dict:
    reason = (reason or "").strip()[:1000]
    if not reason:
        return {"ok": False, "error": "reason required"}
    audit(engine, actor, "community.report", "entry", str(entry_id),
          json.dumps({"reason": reason}))
    return {"ok": True, "entry_id": entry_id}


def rate_state() -> dict:
    return {"ts": time.time()}
