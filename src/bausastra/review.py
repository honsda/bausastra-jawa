"""Review queue for AI + community-drafted definitions.

Drafts are definitions whose text starts with [AI-DRAFT] or [COMMUNITY-DRAFT].
- list_drafts: paginated queue with headword + confidence (parsed from raw JSON)
- approve: strip the prefix -> definition becomes a normal verified gloss
- reject: delete the definition; delete the entry too if it has no
  definitions left AND came from an AI/community source (keeps dictionary clean)
"""
from __future__ import annotations
import json
from sqlalchemy import text as stext

PREFIX = "[AI-DRAFT"
COMMUNITY_PREFIX = "[COMMUNITY-DRAFT"
DRAFT_PREFIXES = (PREFIX, COMMUNITY_PREFIX)
DRAFT_COND = "(definition LIKE '[AI-DRAFT%' OR definition LIKE '[COMMUNITY-DRAFT%')"
DRAFT_COND_D = "(d.definition LIKE '[AI-DRAFT%' OR d.definition LIKE '[COMMUNITY-DRAFT%')"


def _is_draft(text: str | None) -> bool:
    return bool(text) and (text.startswith(PREFIX) or text.startswith(COMMUNITY_PREFIX))

_ENG = None


def _engine():
    """Cache the engine: get_engine() probes Postgres before SQLite fallback."""
    global _ENG
    if _ENG is None:
        from .db import get_engine, is_postgres
        _ENG = get_engine()
    return _ENG


def list_drafts(limit: int = 20, offset: int = 0) -> dict:
    eng = _engine()
    with eng.connect() as c:
        total = c.execute(stext(
            f"SELECT COUNT(*) FROM definitions WHERE {DRAFT_COND}")).scalar() or 0
        rows = c.execute(stext(f"""
            SELECT d.id, d.definition, d.lang, d.context, e.id, e.headword, e.lang,
                   e.speech_level, e.pos, s.name, e.raw
            FROM definitions d
            JOIN entries e ON e.id=d.entry_id
            LEFT JOIN sources s ON s.id=e.source_id
            WHERE {DRAFT_COND_D}
            ORDER BY d.id LIMIT :lim OFFSET :off
        """), {"lim": limit, "off": offset}).all()
    items = []
    for did, defi, dlang, dctx, eid, hw, elang, lvl, pos, src, raw in rows:
        conf = None
        model = None
        for blob in (dctx, raw):
            try:
                r = json.loads(blob) if isinstance(blob, str) and blob else (blob or {})
                conf = conf if conf is not None else r.get("confidence")
                model = model or r.get("ai_model")
            except Exception:
                pass
        items.append({"def_id": did, "definition": defi, "def_lang": dlang,
                      "entry_id": eid, "headword": hw, "lang": elang,
                      "speech_level": lvl, "pos": pos, "source": src,
                      "confidence": conf, "ai_model": model})
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def approve(def_id: int, edited_text: str | None = None) -> dict:
    """Approve a draft: strip prefix (or use edited text). Returns updated row."""
    eng = _engine()
    with eng.begin() as c:
        row = c.execute(stext(
            "SELECT definition FROM definitions WHERE id=:i"), {"i": def_id}).first()
        if not row:
            return {"ok": False, "error": "not found"}
        current = row[0] or ""
        if not _is_draft(current):
            return {"ok": False, "error": "not a draft"}
        if edited_text and edited_text.strip():
            new_text = edited_text.strip()[:4000]
        else:
            new_text = current.split("]", 1)[-1].strip()[:4000] or current
        c.execute(stext("UPDATE definitions SET definition=:d WHERE id=:i"),
                  {"d": new_text, "i": def_id})
        # mark entry raw as reviewed (JSONB on PG, TEXT JSON on SQLite)
        try:
            from .db import is_postgres
            if is_postgres(eng):
                c.execute(stext("""
                    UPDATE entries SET raw = COALESCE(raw, '{}'::jsonb) || '{"reviewed": true}'::jsonb
                    WHERE id=(SELECT entry_id FROM definitions WHERE id=:i)
                """), {"i": def_id})
            else:
                row = c.execute(stext(
                    "SELECT raw FROM entries WHERE id=(SELECT entry_id FROM definitions WHERE id=:i)"),
                    {"i": def_id}).first()
                merged = {"reviewed": True}
                if row and row[0]:
                    try:
                        cur = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
                        if isinstance(cur, dict):
                            merged = {**cur, "reviewed": True}
                    except Exception:
                        pass
                c.execute(stext("UPDATE entries SET raw=:r WHERE id=(SELECT entry_id FROM definitions WHERE id=:i)"),
                          {"r": json.dumps(merged, ensure_ascii=False), "i": def_id})
        except Exception:
            pass  # reviewed flag is best-effort; approval itself already succeeded
    return {"ok": True, "def_id": def_id, "definition": new_text}


def reject(def_id: int) -> dict:
    """Reject a draft: delete definition; drop orphan AI-only entries."""
    eng = _engine()
    with eng.begin() as c:
        row = c.execute(stext(
            "SELECT entry_id, definition FROM definitions WHERE id=:i"),
            {"i": def_id}).first()
        if not row:
            return {"ok": False, "error": "not found"}
        eid, defi = row
        if not _is_draft(defi):
            return {"ok": False, "error": "not a draft"}
        c.execute(stext("DELETE FROM definitions WHERE id=:i"), {"i": def_id})
        left = c.execute(stext(
            "SELECT COUNT(*) FROM definitions WHERE entry_id=:e"), {"e": eid}).scalar() or 0
        dropped_entry = False
        if left == 0:
            src = c.execute(stext(
                "SELECT s.name FROM entries e LEFT JOIN sources s ON s.id=e.source_id "
                "WHERE e.id=:e"), {"e": eid}).first()
            if src and src[0] and (str(src[0]).startswith("ollama")
                                   or str(src[0]) == "community-submissions"):
                c.execute(stext("DELETE FROM entries WHERE id=:e"), {"e": eid})
                dropped_entry = True
    return {"ok": True, "def_id": def_id, "dropped_entry": dropped_entry}


def stats() -> dict:
    eng = _engine()
    with eng.connect() as c:
        drafts = c.execute(stext(
            f"SELECT COUNT(*) FROM definitions WHERE {DRAFT_COND}")).scalar() or 0
        total_defs = c.execute(stext("SELECT COUNT(*) FROM definitions")).scalar() or 0
        entries = c.execute(stext("SELECT COUNT(*) FROM entries")).scalar() or 0
    return {"ai_drafts_pending": drafts, "drafts_pending": drafts,
            "definitions": total_defs, "entries": entries}
