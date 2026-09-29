"""Bausastra JSON API — backend for the Svelte frontend in web/.

Run:  python -m bausastra.cli serve [--port 5000]
Serves /api/* and, if web/dist exists (npm run build), the built frontend.
During dev, run `npm run dev` in web/ (Vite proxies /api to :5000).
"""
from __future__ import annotations
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from sqlalchemy import text as stext

from .db import get_engine
from .javanese import normalize

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "web" / "dist"
PER_PAGE = 50

app = Flask(__name__)
_ENG = None


def _engine():
    """Cache the engine: get_engine() probes Postgres before SQLite fallback."""
    global _ENG
    if _ENG is None:
        _ENG = get_engine()
    return _ENG


def _row_to_item(r) -> dict:
    eid, hw, lang, lvl, src, defi = r
    return {"id": eid, "headword": hw, "lang": lang, "speech_level": lvl,
            "source": src, "definition": defi}


@app.route("/api/stats")
def stats():
    with _engine().connect() as c:
        n = c.execute(stext("SELECT COUNT(*) FROM entries")).scalar() or 0
        rel = c.execute(stext("SELECT COUNT(*) FROM relations")).scalar() or 0
    return jsonify({"entries": n, "relations": rel})


@app.route("/api/letters")
def letters():
    with _engine().connect() as c:
        rows = c.execute(stext(
            "SELECT substr(headword_norm,1,1) AS l, COUNT(*) FROM entries "
            "GROUP BY l ORDER BY l")).all()
    out = [{"letter": r[0].upper(), "count": r[1]} for r in rows if r[0]]
    return jsonify(out)


@app.route("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    try:
        limit = min(100, max(1, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50
    if not q:
        return jsonify([])
    nq = normalize(q)
    with _engine().connect() as c:
        rows = c.execute(stext("""
            SELECT e.id, e.headword, e.lang, e.speech_level, s.name, d.definition
            FROM entries e LEFT JOIN sources s ON s.id=e.source_id
            LEFT JOIN definitions d ON d.entry_id=e.id
            AND d.id=(SELECT MIN(id) FROM definitions WHERE entry_id=e.id)
            WHERE e.headword_norm LIKE :like
            ORDER BY CASE WHEN e.headword_norm=:exact THEN 0
                          WHEN e.headword_norm LIKE :pref THEN 1 ELSE 2 END,
                     e.headword LIMIT :lim
        """), {"like": f"%{nq}%", "exact": nq, "pref": f"{nq}%", "lim": limit}).all()
    return jsonify([_row_to_item(r) for r in rows])


@app.route("/api/letter/<letter>")
def letter(letter: str):
    letter = (letter or "A")[:1].lower()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    with _engine().connect() as c:
        total = c.execute(stext(
            "SELECT COUNT(*) FROM entries WHERE substr(headword_norm,1,1)=:l"),
            {"l": letter}).scalar() or 0
        rows = c.execute(stext("""
            SELECT e.id, e.headword, e.lang, e.speech_level, s.name, d.definition
            FROM entries e LEFT JOIN sources s ON s.id=e.source_id
            LEFT JOIN definitions d ON d.entry_id=e.id
            AND d.id=(SELECT MIN(id) FROM definitions WHERE entry_id=e.id)
            WHERE substr(e.headword_norm,1,1)=:l
            ORDER BY e.headword_norm LIMIT :lim OFFSET :off
        """), {"l": letter, "lim": PER_PAGE, "off": (page - 1) * PER_PAGE}).all()
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return jsonify({"letter": letter.upper(), "page": page, "pages": pages,
                    "total": total, "items": [_row_to_item(r) for r in rows]})


@app.route("/api/word/<int:eid>")
def word(eid: int):
    with _engine().connect() as c:
        e = c.execute(stext("""
            SELECT e.headword, e.lang, e.pos, e.speech_level, e.aksara_jawa, s.name
            FROM entries e LEFT JOIN sources s ON s.id=e.source_id WHERE e.id=:i
        """), {"i": eid}).first()
        if not e:
            return jsonify({"error": "not found"}), 404
        hw, lang, pos, lvl, aks, src = e
        defs = c.execute(stext(
            "SELECT lang, definition FROM definitions WHERE entry_id=:i ORDER BY id"),
            {"i": eid}).all()
        syns = c.execute(stext("""
            SELECT e2.id, e2.headword FROM relations r
            JOIN entries e2 ON e2.id=r.to_entry_id
            WHERE r.from_entry_id=:i AND r.rel_type='synonym' LIMIT 30
        """), {"i": eid}).all()
    return jsonify({"id": eid, "headword": hw, "lang": lang, "pos": pos,
                    "speech_level": lvl, "aksara_jawa": aks, "source": src,
                    "definitions": [{"lang": dl, "text": dd} for dl, dd in defs],
                    "synonyms": [{"id": i, "headword": h} for i, h in syns]})


# --- serve built frontend (same origin, no CORS needed) ---
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def frontend(path: str):
    if DIST.is_dir() and (DIST / "index.html").exists():
        target = DIST / path
        if path and target.is_file():
            return send_from_directory(DIST, path)
        return send_from_directory(DIST, "index.html")
    return (jsonify({"ok": True, "message": "API running. Build the frontend: cd web && npm install && npm run build",
                     "endpoints": ["/api/stats", "/api/letters", "/api/search?q=", "/api/letter/A", "/api/word/1"]}), 200)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
