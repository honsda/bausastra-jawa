"""Bausastra JSON API — backend for the Svelte frontend in web/.

Public, versioned endpoints live under /api/v1/* (stable contract, documented
at /api/docs). Unversioned /api/* aliases are kept for the bundled frontend.
Transliteration (Latin <-> Aksara Jawa) uses the sinau tables.

Run:  python -m bausastra.cli serve [--port 5000]
Serves /api/* and, if web/dist exists (npm run build), the built frontend.
During dev, run `npm run dev` in web/ (Vite proxies /api to :5000).
"""
from __future__ import annotations
import logging
import os
import time
from collections import defaultdict, deque
from functools import wraps
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from sqlalchemy import text as stext

from .db import get_engine, is_postgres
from .javanese import normalize
from .transliterate import transliterate, reverse_transliterate
from . import review as _review
from . import ai_worker as _worker
from . import public as _pub

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "web" / "dist"
PER_PAGE = 50
MAX_Q_LEN = 200
MAX_TEXT_LEN = 2000
RATE_LIMIT_PER_MIN = 120
SUBMIT_ANON_PER_HOUR = 5
SUBMIT_KEY_PER_HOUR = 100

ADMIN_TOKEN = os.getenv("BAUSASTRA_ADMIN_TOKEN") or os.getenv("ADMIN_TOKEN") or ""
REDIS_URL = os.getenv("REDIS_URL", "")

app = Flask(__name__)
try:  # optional CORS for external /api/v1 clients; same-origin frontend needs none
    from flask_cors import CORS
    CORS(app, resources={r"/api/*": {"origins": os.getenv("CORS_ORIGINS", "*")}})
except ImportError:
    @app.after_request
    def _cors_fallback(resp):
        resp.headers.setdefault("Access-Control-Allow-Origin", os.getenv("CORS_ORIGINS", "*"))
        return resp

_ENG = None
_CACHE: dict[str, tuple[float, object]] = {}
_RATE: dict[str, deque] = defaultdict(deque)
_REDIS = None


def _redis():
    """Lazy Redis client or None (falls back to in-memory limits)."""
    global _REDIS
    if _REDIS is False:
        return None
    if _REDIS is not None:
        return _REDIS
    if not REDIS_URL:
        _REDIS = False
        return None
    try:
        import redis
        _REDIS = redis.from_url(REDIS_URL, socket_timeout=2)
        _REDIS.ping()
        return _REDIS
    except Exception as e:
        log.warning("Redis unavailable (%s); using in-memory limits.", e)
        _REDIS = False
        return None


def _rate_allow(bucket: str, limit: int, window_sec: int) -> bool:
    """Shared limiter: Redis INCR+EXPIRE when configured, else in-memory deque."""
    r = _redis()
    now = time.time()
    if r is not None:
        try:
            key = f"bausastra:rl:{bucket}"
            n = r.incr(key)
            if n == 1:
                r.expire(key, window_sec)
            return n <= limit
        except Exception:
            pass
    q = _RATE[f"{bucket}:{window_sec}"]
    while q and now - q[0] > window_sec:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _check_rate() -> bool:
    """Default per-IP read limiter (120/min)."""
    ip = request.remote_addr or "unknown"
    return _rate_allow(f"read:{ip}", RATE_LIMIT_PER_MIN, 60)


def _limited(fn):
    @wraps(fn)
    def inner(*a, **kw):
        if not _check_rate():
            return jsonify({"ok": False, "error": "rate limited, retry later"}), 429
        return fn(*a, **kw)
    return inner


def _require_admin():
    """401 unless BAUSASTRA_ADMIN_TOKEN matches (header, bearer, or JSON)."""
    if not ADMIN_TOKEN:
        return None  # open mode for local dev; set token in prod
    sent = request.headers.get("X-Admin-Token", "")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        sent = sent or auth[7:]
    if not sent and request.is_json:
        try:
            sent = (request.get_json(silent=True) or {}).get("token", "") or sent
        except Exception:
            pass
    sent = sent or request.args.get("token", "")
    if sent != ADMIN_TOKEN:
        return jsonify({"ok": False, "error": "unauthorized (admin token required)"}), 401
    return None


def _api_key_identity() -> dict | None:
    """Return verified API-key identity or None (public/anonymous)."""
    raw = request.headers.get("X-API-Key", "")
    auth = request.headers.get("Authorization", "")
    if not raw and auth.lower().startswith("bearer baus_"):
        raw = auth[7:]
    if not raw and request.is_json:
        try:
            raw = (request.get_json(silent=True) or {}).get("api_key", "") or raw
        except Exception:
            pass
    if not raw:
        raw = request.args.get("api_key", "")
    if not raw:
        return None
    try:
        return _pub.verify_key(_engine(), raw)
    except Exception as e:
        log.warning("api-key verify failed: %s", e)
        return None


def _actor() -> str:
    """Audit actor label: admin, key:name(prefix), or ip:address."""
    if ADMIN_TOKEN:
        sent = request.headers.get("X-Admin-Token", "")
        if sent == ADMIN_TOKEN:
            return "admin"
    ident = _api_key_identity()
    if ident:
        return f"key:{ident['name']}({ident['prefix']})"
    return f"ip:{request.remote_addr or 'unknown'}"


def _engine():
    """Cache the engine: get_engine() probes Postgres before SQLite fallback."""
    global _ENG
    if _ENG is None:
        _ENG = get_engine()
    return _ENG


def _row_to_item(r) -> dict:
    eid, hw, lang, lvl, src, defi, aks = r
    if not aks and lang in ("jv", "kawi"):
        aks = transliterate(hw or "") or None  # auto-transliterate on read
    return {"id": eid, "headword": hw, "lang": lang, "speech_level": lvl,
            "source": src, "definition": defi, "aksara_jawa": aks}


def _cached(key: str, ttl: float, fn):
    now = time.time()
    if key in _CACHE and now - _CACHE[key][0] < ttl:
        return _CACHE[key][1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _do_stats() -> dict:
    def _q():
        try:
            with _engine().connect() as c:
                n = c.execute(stext("SELECT COUNT(*) FROM entries")).scalar() or 0
                rel = c.execute(stext("SELECT COUNT(*) FROM relations")).scalar() or 0
        except Exception as e:
            log.warning("stats query failed: %s", e)
            return {"entries": 0, "relations": 0}
        return {"entries": n, "relations": rel}
    return _cached("stats", 30.0, _q)


def _do_letters() -> list:
    def _q():
        try:
            with _engine().connect() as c:
                rows = c.execute(stext(
                    "SELECT substr(headword_norm,1,1) AS l, COUNT(*) FROM entries "
                    "GROUP BY l ORDER BY l")).all()
        except Exception as e:
            log.warning("letters query failed: %s", e)
            return []
        return [{"letter": (r[0] or "").upper(), "count": r[1]} for r in rows if r[0]]
    return _cached("letters", 60.0, _q)


def _do_search(q: str, limit: int = 50) -> list:
    q = (q or "").strip()[:MAX_Q_LEN]
    limit = min(100, max(1, limit))
    if not q:
        return []
    nq = normalize(q)
    try:
        with _engine().connect() as c:
            if is_postgres(_engine()):
                try:
                    rows = c.execute(stext("""
                        SELECT e.id, e.headword, e.lang, e.speech_level, s.name, d.definition, e.aksara_jawa,
                               similarity(e.headword_norm, :exact) AS sim
                        FROM entries e LEFT JOIN sources s ON s.id=e.source_id
                        LEFT JOIN definitions d ON d.entry_id=e.id
                        AND d.id=(SELECT MIN(id) FROM definitions WHERE entry_id=e.id)
                        WHERE e.headword_norm LIKE :like OR e.headword_norm % :exact
                        ORDER BY CASE WHEN e.headword_norm=:exact THEN 0
                                      WHEN e.headword_norm LIKE :pref THEN 1 ELSE 2 END,
                                 sim DESC, e.headword LIMIT :lim
                    """), {"like": f"%{nq}%", "exact": nq, "pref": f"{nq}%", "lim": limit}).all()
                    return [_row_to_item(r[:7]) for r in rows]
                except Exception:
                    pass  # pg_trgm missing? fall through to LIKE query
            rows = c.execute(stext("""
                SELECT e.id, e.headword, e.lang, e.speech_level, s.name, d.definition, e.aksara_jawa
                FROM entries e LEFT JOIN sources s ON s.id=e.source_id
                LEFT JOIN definitions d ON d.entry_id=e.id
                AND d.id=(SELECT MIN(id) FROM definitions WHERE entry_id=e.id)
                WHERE e.headword_norm LIKE :like
                ORDER BY CASE WHEN e.headword_norm=:exact THEN 0
                              WHEN e.headword_norm LIKE :pref THEN 1 ELSE 2 END,
                         e.headword LIMIT :lim
            """), {"like": f"%{nq}%", "exact": nq, "pref": f"{nq}%", "lim": limit}).all()
    except Exception as e:
        log.warning("search failed for %r: %s", q, e)
        return []
    return [_row_to_item(r) for r in rows]


def _do_letter(letter: str, page: int = 1) -> dict:
    letter = (letter or "A")[:1].lower()
    page = max(1, min(page, 10000))
    try:
        with _engine().connect() as c:
            total = c.execute(stext(
                "SELECT COUNT(*) FROM entries WHERE substr(headword_norm,1,1)=:l"),
                {"l": letter}).scalar() or 0
            rows = c.execute(stext("""
                SELECT e.id, e.headword, e.lang, e.speech_level, s.name, d.definition, e.aksara_jawa
                FROM entries e LEFT JOIN sources s ON s.id=e.source_id
                LEFT JOIN definitions d ON d.entry_id=e.id
                AND d.id=(SELECT MIN(id) FROM definitions WHERE entry_id=e.id)
                WHERE substr(e.headword_norm,1,1)=:l
                ORDER BY e.headword_norm LIMIT :lim OFFSET :off
            """), {"l": letter, "lim": PER_PAGE, "off": (page - 1) * PER_PAGE}).all()
    except Exception as e:
        log.warning("letter query failed: %s", e)
        return {"letter": letter.upper(), "page": page, "pages": 1, "total": 0, "items": []}
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return {"letter": letter.upper(), "page": page, "pages": pages,
            "total": total, "items": [_row_to_item(r) for r in rows]}


def _do_word(eid: int) -> tuple[dict | None, str | None]:
    try:
        eid = int(eid)
    except (TypeError, ValueError):
        return None, "invalid id"
    if eid <= 0 or eid > 2_147_483_647:
        return None, "invalid id"
    try:
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
    except Exception as ex:
        log.warning("word %s failed: %s", eid, ex)
        return None, "lookup failed"
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
@_limited
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
@_limited
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
@_limited
def v1_transliterate():
    text = (request.get_json(silent=True) or {}).get("text") if request.method == "POST" else None
    text = text if text is not None else request.args.get("text", "")
    text = (text or "")[:MAX_TEXT_LEN]
    if not text:
        return jsonify({"ok": False, "error": "provide ?text= or JSON {\"text\": ...}"}), 400
    return jsonify({"ok": True, "data": {"text": text, "aksara": transliterate(text)}})


@app.route("/api/v1/reverse", methods=["GET", "POST"])
@_limited
def v1_reverse():
    text = (request.get_json(silent=True) or {}).get("text") if request.method == "POST" else None
    text = text if text is not None else request.args.get("text", "")
    text = (text or "")[:MAX_TEXT_LEN]
    if not text:
        return jsonify({"ok": False, "error": "provide ?text= or JSON {\"text\": ...}"}), 400
    return jsonify({"ok": True, "data": {"text": text, "latin": reverse_transliterate(text)}})


# --- review desk: approve/reject AI drafts ---
@app.route("/api/v1/review/queue")
def v1_review_queue():
    try:
        limit = min(100, max(1, _int_param("limit", 20)))
        offset = max(0, _int_param("offset", 0))
    except Exception:
        limit, offset = 20, 0
    return jsonify({"ok": True, "data": _review.list_drafts(limit, offset)})


@app.route("/api/v1/review/stats")
def v1_review_stats():
    return jsonify({"ok": True, "data": _review.stats()})


@app.route("/api/v1/review/approve", methods=["POST"])
def v1_review_approve():
    denied = _require_admin()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    did = body.get("id") or request.args.get("id")
    try:
        did = int(did)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "provide JSON {\"id\": def_id}"}), 400
    out = _review.approve(did, body.get("text"))
    if out.get("ok"):
        _pub.audit(_engine(), _actor(), "review.approve", "definition", str(did),
                   (body.get("text") or "")[:500])
    return jsonify({"ok": out.get("ok", False), "data": out,
                    "error": out.get("error")}), 200 if out.get("ok") else 404


@app.route("/api/v1/review/reject", methods=["POST"])
def v1_review_reject():
    denied = _require_admin()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    did = body.get("id") or request.args.get("id")
    try:
        did = int(did)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "provide JSON {\"id\": def_id}"}), 400
    out = _review.reject(did)
    if out.get("ok"):
        _pub.audit(_engine(), _actor(), "review.reject", "definition", str(did), "")
    return jsonify({"ok": out.get("ok", False), "data": out,
                    "error": out.get("error")}), 200 if out.get("ok") else 404


# --- AI background worker: drafts while you review ---
@app.route("/api/v1/ai/status")
def v1_ai_status():
    return jsonify({"ok": True, "data": _worker.status()})


@app.route("/api/v1/ai/start", methods=["POST"])
def v1_ai_start():
    denied = _require_admin()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    try:
        limit = min(30, max(1, int(body.get("limit", 5))))
    except (TypeError, ValueError):
        limit = 5
    try:
        interval = min(600, max(5, float(body.get("interval", 20))))
    except (TypeError, ValueError):
        interval = 20
    raw_words = body.get("words", "")
    if isinstance(raw_words, str):
        words = [w.strip() for w in raw_words.split(",") if w.strip()] or None
    else:
        words = None
    return jsonify({"ok": True, "data": _worker.start(limit, interval, words)})


@app.route("/api/v1/ai/stop", methods=["POST"])
def v1_ai_stop():
    denied = _require_admin()
    if denied:
        return denied
    return jsonify({"ok": True, "data": _worker.stop()})


# --- public contributions: submit / report (open, tiered limits) ---
@app.route("/api/v1/submit", methods=["POST"])
def v1_submit():
    body = request.get_json(silent=True) or {}
    clean, err = _pub.validate_submit(body)
    if err:
        status = 400 if err not in ("spam detected",) else 200
        return jsonify({"ok": False, "error": err}), status
    ident = _api_key_identity()
    actor = f"key:{ident['name']}({ident['prefix']})" if ident else f"ip:{request.remote_addr or 'unknown'}"
    bucket = f"submit:{ident['prefix']}" if ident else f"submit-anon:{request.remote_addr or 'unknown'}"
    limit = SUBMIT_KEY_PER_HOUR if ident else SUBMIT_ANON_PER_HOUR
    if not _rate_allow(bucket, limit, 3600):
        return jsonify({"ok": False, "error": f"rate limited ({limit}/hour for this tier)"}), 429
    try:
        out = _pub.submit_word(_engine(), clean, actor)
    except Exception as e:
        log.warning("submit failed: %s", e)
        return jsonify({"ok": False, "error": "submit failed"}), 500
    if not out.get("ok"):
        return jsonify({"ok": False, "error": out.get("error", "submit failed")}), 500
    _CACHE.pop("stats", None)
    return jsonify({"ok": True, "data": out}), 201


@app.route("/api/v1/report", methods=["POST"])
def v1_report():
    body = request.get_json(silent=True) or {}
    try:
        eid = int(body.get("id") or body.get("entry_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "provide JSON {\"id\": entry_id, \"reason\": ...}"}), 400
    reason = (body.get("reason") or "").strip()
    if eid <= 0 or not reason:
        return jsonify({"ok": False, "error": "provide JSON {\"id\": entry_id, \"reason\": ...}"}), 400
    if not _rate_allow(f"report:{request.remote_addr or 'unknown'}", 20, 3600):
        return jsonify({"ok": False, "error": "rate limited (20/hour)"}), 429
    out = _pub.report_wrong(_engine(), eid, reason, _actor())
    if not out.get("ok"):
        return jsonify({"ok": False, "error": out.get("error", "report failed")}), 400
    return jsonify({"ok": True, "data": out})


@app.route("/api/v1/sources")
def v1_sources():
    def _q():
        try:
            with _engine().connect() as c:
                rows = c.execute(stext(
                    "SELECT s.name, s.url, s.kind, s.language_pair, COUNT(e.id) "
                    "FROM sources s LEFT JOIN entries e ON e.source_id=s.id "
                    "GROUP BY s.name, s.url, s.kind, s.language_pair ORDER BY s.name")).all()
        except Exception as e:
            log.warning("sources query failed: %s", e)
            return []
        return [{"name": r[0], "url": r[1], "kind": r[2],
                 "language_pair": r[3], "entries": r[4]} for r in rows]
    return jsonify({"ok": True, "data": _cached("sources", 120.0, _q)})


@app.route("/api/v1/export")
@_limited
def v1_export():
    fmt = (request.args.get("format", "json") or "json").lower()
    try:
        limit = min(5000, max(1, int(request.args.get("limit", 500))))
    except (TypeError, ValueError):
        limit = 500
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    try:
        with _engine().connect() as c:
            rows = c.execute(stext(
                "SELECT e.headword, e.lang, e.speech_level, e.aksara_jawa, d.definition, d.lang "
                "FROM entries e LEFT JOIN definitions d ON d.entry_id=e.id "
                f"WHERE d.definition NOT LIKE '[AI-DRAFT%' AND d.definition NOT LIKE '[COMMUNITY-DRAFT%' "
                "ORDER BY e.id LIMIT :lim OFFSET :off"),
                {"lim": limit, "off": offset}).all()
    except Exception as e:
        log.warning("export failed: %s", e)
        return jsonify({"ok": False, "error": "export failed"}), 500
    items = [{"headword": r[0], "lang": r[1], "speech_level": r[2],
              "aksara_jawa": r[3], "definition": r[4], "def_lang": r[5]} for r in rows]
    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["headword", "lang", "speech_level",
                                            "aksara_jawa", "definition", "def_lang"])
        w.writeheader()
        w.writerows(items)
        return app.response_class(buf.getvalue(), mimetype="text/csv",
                                  headers={"Content-Disposition": "attachment; filename=bausastra.csv"})
    return jsonify({"ok": True, "data": {"items": items, "limit": limit, "offset": offset}})


# --- API keys: self-serve minting is rate-limited; admin manages ---
@app.route("/api/v1/keys", methods=["POST"])
def v1_keys_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()[:200]
    if len(name) < 2:
        return jsonify({"ok": False, "error": "provide JSON {\"name\": \"your app name\"}"}), 400
    if not _rate_allow(f"keymint:{request.remote_addr or 'unknown'}", 5, 3600):
        return jsonify({"ok": False, "error": "rate limited (5 keys/hour/IP)"}), 429
    try:
        out = _pub.create_key(_engine(), name)
    except Exception as e:
        log.warning("key mint failed: %s", e)
        return jsonify({"ok": False, "error": "key mint failed"}), 500
    out = dict(out)
    out["note"] = "store raw_key now — it is never shown again"
    return jsonify({"ok": True, "data": out}), 201


@app.route("/api/v1/keys", methods=["GET"])
def v1_keys_list():
    denied = _require_admin()
    if denied:
        return denied
    return jsonify({"ok": True, "data": _pub.list_keys(_engine())})


@app.route("/api/v1/keys/revoke", methods=["POST"])
def v1_keys_revoke():
    denied = _require_admin()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    ident = str(body.get("id") or body.get("prefix") or "").strip()
    if not ident:
        return jsonify({"ok": False, "error": "provide JSON {\"id\": or \"prefix\": ...}"}), 400
    ok = _pub.revoke_key(_engine(), ident)
    _pub.audit(_engine(), _actor(), "apikey.revoke", "api_key", ident, "")
    return jsonify({"ok": ok, "data": {"revoked": ok}})


# --- health + OpenAPI ---
@app.route("/healthz")
def healthz():
    info: dict = {"ok": True, "version": "0.1.0"}
    try:
        from . import __init__ as _pkg  # noqa
    except Exception:
        pass
    try:
        with _engine().connect() as c:
            c.execute(stext("SELECT 1"))
            n = c.execute(stext("SELECT COUNT(*) FROM entries")).scalar() or 0
        info.update({"db": "up", "entries": n, "redis": "up" if _redis() else "off"})
    except Exception as e:
        info.update({"ok": False, "db": f"down: {e}"})
        return jsonify(info), 503
    return jsonify(info)


@app.route("/api/openapi.json")
def openapi():
    return jsonify(_openapi_spec())


def _openapi_spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Bausastra API", "version": "v1",
                 "description": "Javanese dictionary (ngoko/krama/kawi) + Aksara Jawa transliteration."},
        "servers": [{"url": "/"}],
        "paths": {
            "/healthz": {"get": {"summary": "health check", "responses": {"200": {"description": "ok"}}}},
            "/api/openapi.json": {"get": {"summary": "this OpenAPI spec", "responses": {"200": {"description": "ok"}}}},
            "/api/v1/search": {"get": {"summary": "ranked search",
                "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string", "maxLength": MAX_Q_LEN}},
                               {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50}}],
                "responses": {"200": {"description": "results"}, "429": {"description": "rate limited"}}}},
            "/api/v1/letter/{letter}": {"get": {"summary": "browse by initial",
                "parameters": [{"name": "letter", "in": "path", "required": True, "schema": {"type": "string"}},
                               {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}}],
                "responses": {"200": {"description": "page"}}}},
            "/api/v1/letters": {"get": {"summary": "letter categories", "responses": {"200": {"description": "ok"}}}},
            "/api/v1/word/{id}": {"get": {"summary": "entry detail",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "ok"}, "404": {"description": "not found"}}}},
            "/api/v1/transliterate": {"get": {"summary": "Latin -> Aksara",
                "parameters": [{"name": "text", "in": "query", "required": True, "schema": {"type": "string", "maxLength": MAX_TEXT_LEN}}],
                "responses": {"200": {"description": "ok"}}},
                "post": {"summary": "Latin -> Aksara (JSON {text})", "responses": {"200": {"description": "ok"}}}},
            "/api/v1/reverse": {"get": {"summary": "Aksara -> Latin",
                "parameters": [{"name": "text", "in": "query", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "ok"}}},
                "post": {"summary": "Aksara -> Latin (JSON {text})", "responses": {"200": {"description": "ok"}}}},
            "/api/v1/submit": {"post": {"summary": "submit a word (community draft, needs review)",
                "responses": {"201": {"description": "queued"}, "400": {"description": "invalid"}, "429": {"description": "rate limited"}}}},
            "/api/v1/report": {"post": {"summary": "report a wrong entry",
                "responses": {"200": {"description": "logged"}}}},
            "/api/v1/sources": {"get": {"summary": "source provenance + counts", "responses": {"200": {"description": "ok"}}}},
            "/api/v1/export": {"get": {"summary": "verified dump (json/csv, max 5000)",
                "parameters": [{"name": "format", "in": "query", "schema": {"type": "string", "enum": ["json", "csv"]}},
                               {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 500}},
                               {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0}}],
                "responses": {"200": {"description": "ok"}}}},
            "/api/v1/keys": {
                "post": {"summary": "mint an API key (5/hour/IP, store raw_key once)", "responses": {"201": {"description": "created"}}},
                "get": {"summary": "list keys (admin)", "responses": {"200": {"description": "ok"}, "401": {"description": "unauthorized"}}}},
            "/api/v1/keys/revoke": {"post": {"summary": "revoke a key (admin)", "responses": {"200": {"description": "ok"}}}},
            "/api/v1/stats": {"get": {"summary": "counts", "responses": {"200": {"description": "ok"}}}},
            "/api/v1/review/queue": {"get": {"summary": "draft queue (AI + community)", "responses": {"200": {"description": "ok"}}}},
            "/api/v1/review/approve": {"post": {"summary": "approve draft (admin)", "responses": {"200": {"description": "ok"}}}},
            "/api/v1/review/reject": {"post": {"summary": "reject draft (admin)", "responses": {"200": {"description": "ok"}}}},
        },
    }


# --- review desk web page (no build step needed) ---
@app.route("/review")
def review_page():
    html = ROOT / "src" / "bausastra" / "review_page.html"
    if html.exists():
        return send_from_directory(html.parent, html.name)
    return jsonify({"ok": False, "error": "review page missing"}), 404


@app.route("/api/docs")
def docs():  return jsonify({
        "name": "Bausastra API", "version": "v1", "transliteration": "sinau tables (honsda/sinau)",
        "openapi": "/api/openapi.json",
        "auth": "reads are open; writes: X-API-Key header (POST /api/v1/keys) for submit/report limits, BAUSASTRA_ADMIN_TOKEN for review/keys",
        "endpoints": [
            {"method": "GET", "path": "/healthz", "desc": "health check (db + counts)"},
            {"method": "GET", "path": "/api/v1/search?q=wonten&limit=50", "desc": "ranked dictionary search (exact, prefix, contains)"},
            {"method": "GET", "path": "/api/v1/letter/W?page=1", "desc": "browse by initial letter, 50/page"},
            {"method": "GET", "path": "/api/v1/letters", "desc": "initial-letter categories with counts"},
            {"method": "GET", "path": "/api/v1/word/5184", "desc": "entry detail: definitions, speech level, aksara_jawa, synonyms"},
            {"method": "GET|POST", "path": "/api/v1/transliterate", "desc": "Latin Javanese -> Aksara Jawa (?text= or JSON body)"},
            {"method": "GET|POST", "path": "/api/v1/reverse", "desc": "Aksara Jawa -> Latin (?text= or JSON body)"},
            {"method": "GET", "path": "/api/v1/stats", "desc": "entry/relation counts"},
            {"method": "POST", "path": "/api/v1/submit", "desc": "submit a word: JSON {headword, definition, lang?, def_lang?, speech_level?, pos?, example_jv?, example_id?} -> community draft"},
            {"method": "POST", "path": "/api/v1/report", "desc": "report wrong entry: JSON {id, reason}"},
            {"method": "GET", "path": "/api/v1/sources", "desc": "source provenance + entry counts"},
            {"method": "GET", "path": "/api/v1/export?format=json&limit=500", "desc": "verified dump (json/csv, max 5000)"},
            {"method": "POST", "path": "/api/v1/keys", "desc": "mint API key: JSON {name} (5/hour/IP)"},
            {"method": "GET", "path": "/api/v1/keys", "desc": "list keys (admin)"},
            {"method": "POST", "path": "/api/v1/keys/revoke", "desc": "revoke key (admin): JSON {id|prefix}"},
            {"method": "GET", "path": "/api/v1/review/queue?limit=20", "desc": "draft review queue (AI + community)"},
            {"method": "POST", "path": "/api/v1/review/approve", "desc": "approve draft: JSON {\"id\":, \"text\":} (text optional edit)"},
            {"method": "POST", "path": "/api/v1/review/reject", "desc": "reject draft: JSON {\"id\":}"},
            {"method": "GET", "path": "/api/v1/ai/status", "desc": "background AI worker status"},
            {"method": "POST", "path": "/api/v1/ai/start", "desc": "start worker: JSON {\"limit\":5, \"interval\":20, \"words\":\"a,b\"}"},
            {"method": "POST", "path": "/api/v1/ai/stop", "desc": "stop worker after current word"},
            {"method": "GET", "path": "/review", "desc": "human review desk (live queue + worker controls)"},
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
        # never cache index.html: it pins hashed asset names, stale HTML = stale app
        return send_from_directory(DIST, "index.html", max_age=0)
    return (jsonify({"ok": True, "message": "API running. Build the frontend: cd web && npm install && npm run build",
                     "endpoints": ["/api/stats", "/api/letters", "/api/search?q=", "/api/letter/A", "/api/word/1"]}), 200)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
