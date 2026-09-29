"""Bausastra JSON API — backend for the Svelte frontend in web/.

Public, versioned endpoints live under /api/v1/* (stable contract, documented
at /api/docs). Unversioned /api/* aliases are kept for the bundled frontend.
Transliteration (Latin <-> Aksara Jawa) uses the sinau tables.

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
from .transliterate import transliterate, reverse_transliterate

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


def _do_stats() -> dict:
    with _engine().connect() as c:
        n = c.execute(stext("SELECT COUNT(*) FROM entries")).scalar() or 0
        rel = c.execute(stext("SELECT COUNT(*) FROM relations")).scalar() or 0
    return {"entries": n, "relations": rel}


def _do_letters() -> list:
    with _engine().connect() as c:
        rows = c.execute(stext(
            "SELECT substr(headword_norm,1,1) AS l, COUNT(*) FROM entries "
            "GROUP BY l ORDER BY l")).all()
    return [{"letter": r[0].upper(), "count": r[1]} for r in rows if r[0]]


def _do_search(q: str, limit: int = 50) -> list:
    q = (q or "").strip()
    limit = min(100, max(1, limit))
    if not q:
        return []
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
    return [_row_to_item(r) for r in rows]


def _do_letter(letter: str, page: int = 1) -> dict:
    letter = (letter or "A")[:1].lower()
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
    return {"letter": letter.upper(), "page": page, "pages": pages,
            "total": total, "items": [_row_to_item(r) for r in rows]}


def _do_word(eid: int) -> tuple[dict | None, str | None]:
    with _engine().connect() as c:
        e = c.execute(stext("""
            SELECT e.headword, e.lang, e.pos, e.speech_level, e.aksara_jawa, s.name
            FROM entries e LEFT JOIN sources s ON s.id=e.source_id WHERE e.id=:i
        """), {"i": eid}).first()
        if not e:
            return None, "not found"
        hw, lang, pos, lvl, aks, src = e
        if not aks and lang in ("jv", "kawi"):
            aks = transliterate(hw or "") or None  # auto-transliterate on read
        defs = c.execute(stext(
            "SELECT lang, definition FROM definitions WHERE entry_id=:i ORDER BY id"),
            {"i": eid}).all()
        syns = c.execute(stext("""
            SELECT e2.id, e2.headword FROM relations r
            JOIN entries e2 ON e2.id=r.to_entry_id
            WHERE r.from_entry_id=:i AND r.rel_type='synonym' LIMIT 30
        """), {"i": eid}).all()
    return ({"id": eid, "headword": hw, "lang": lang, "pos": pos,
             "speech_level": lvl, "aksara_jawa": aks, "source": src,
             "definitions": [{"lang": dl, "text": dd} for dl, dd in defs],
             "synonyms": [{"id": i, "headword": h} for i, h in syns]}, None)


def _int_param(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except ValueError:
        return default


# --- unversioned routes (bundled frontend) ---
@app.route("/api/stats")
def stats():
    return jsonify(_do_stats())


@app.route("/api/letters")
def letters():
    return jsonify(_do_letters())


@app.route("/api/search")
def search():
    return jsonify(_do_search(request.args.get("q"), _int_param("limit", 50)))


@app.route("/api/letter/<letter>")
def letter(letter: str):
    return jsonify(_do_letter(letter, max(1, _int_param("page", 1))))


@app.route("/api/word/<int:eid>")
def word(eid: int):
    data, err = _do_word(eid)
    if err:
        return jsonify({"error": err}), 404
    return jsonify(data)


# --- v1: stable public contract ---
@app.route("/api/v1/stats")
def v1_stats():
    return jsonify({"ok": True, "data": _do_stats()})


@app.route("/api/v1/letters")
def v1_letters():
    return jsonify({"ok": True, "data": _do_letters()})


@app.route("/api/v1/search")
def v1_search():
    return jsonify({"ok": True, "data": _do_search(request.args.get("q"), _int_param("limit", 50))})


@app.route("/api/v1/letter/<letter>")
def v1_letter(letter: str):
    return jsonify({"ok": True, "data": _do_letter(letter, max(1, _int_param("page", 1)))})


@app.route("/api/v1/word/<int:eid>")
def v1_word(eid: int):
    data, err = _do_word(eid)
    if err:
        return jsonify({"ok": False, "error": err}), 404
    return jsonify({"ok": True, "data": data})


@app.route("/api/v1/transliterate", methods=["GET", "POST"])
def v1_transliterate():
    text = (request.get_json(silent=True) or {}).get("text") if request.method == "POST" else None
    text = text if text is not None else request.args.get("text", "")
    if not text:
        return jsonify({"ok": False, "error": "provide ?text= or JSON {\"text\": ...}"}), 400
    return jsonify({"ok": True, "data": {"text": text, "aksara": transliterate(text)}})


@app.route("/api/v1/reverse", methods=["GET", "POST"])
def v1_reverse():
    text = (request.get_json(silent=True) or {}).get("text") if request.method == "POST" else None
    text = text if text is not None else request.args.get("text", "")
    if not text:
        return jsonify({"ok": False, "error": "provide ?text= or JSON {\"text\": ...}"}), 400
    return jsonify({"ok": True, "data": {"text": text, "latin": reverse_transliterate(text)}})


@app.route("/api/docs")
def docs():
    return jsonify({
        "name": "Bausastra API", "version": "v1", "transliteration": "sinau tables (honsda/sinau)",
        "endpoints": [
            {"method": "GET", "path": "/api/v1/search?q=wonten&limit=50", "desc": "ranked dictionary search (exact, prefix, contains)"},
            {"method": "GET", "path": "/api/v1/letter/W?page=1", "desc": "browse by initial letter, 50/page"},
            {"method": "GET", "path": "/api/v1/letters", "desc": "initial-letter categories with counts"},
            {"method": "GET", "path": "/api/v1/word/5184", "desc": "entry detail: definitions, speech level, aksara_jawa, synonyms"},
            {"method": "GET|POST", "path": "/api/v1/transliterate", "desc": "Latin Javanese -> Aksara Jawa (?text= or JSON body)"},
            {"method": "GET|POST", "path": "/api/v1/reverse", "desc": "Aksara Jawa -> Latin (?text= or JSON body)"},
            {"method": "GET", "path": "/api/v1/stats", "desc": "entry/relation counts"},
        ],
        "examples": {
            "curl_search": "curl 'http://127.0.0.1:5000/api/v1/search?q=wonten'",
            "curl_transliterate": "curl 'http://127.0.0.1:5000/api/v1/transliterate?text=sugeng%20rawuh'",
            "python": "import requests; requests.get('http://127.0.0.1:5000/api/v1/word/5184').json()",
            "javascript": "fetch('/api/v1/search?q=banyu').then(r=>r.json())",
        },
    })


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
