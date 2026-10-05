"""Background AI worker: keeps drafting definitions while you review.

Runs in a daemon thread inside the Flask process (same device, same DB).
- start(limit_per_round, interval_sec, words): begins looping
- stop(): halts after the current word finishes
- status(): running flag + rounds + drafted + errors + current word

Each round: ai_enrich.scrape(words, limit) -> base.save_words(...).
Ollama must be running (http://localhost:11434) with qwen2.5:3b pulled.
"""
from __future__ import annotations
import sys
import threading
import time
import traceback
from pathlib import Path

_STATE = {"running": False, "rounds": 0, "drafted": 0, "errors": 0,
          "current_word": None, "last_error": None, "started_at": None}
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()


def _ensure_scrapper_importable():
    root = Path(__file__).resolve().parents[2]  # repo root
    for p in (str(root), str(root / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)


def _run_loop(limit: int, interval: float, words: list[str] | None):
    global _STATE
    _ensure_scrapper_importable()
    from bausastra.db import get_engine
    from scrapper import ai_enrich
    first = True
    while True:
        with _LOCK:
            if not _STATE["running"]:
                break
        try:
            batch_words = words if (words and first) else None
            first = False
            eng = get_engine()
            n = ai_enrich.run_and_save(eng, batch_words, limit)
            with _LOCK:
                _STATE["drafted"] += n
            with _LOCK:
                _STATE["rounds"] += 1
                _STATE["current_word"] = None
        except Exception as e:  # noqa: BLE001 - worker must survive errors
            with _LOCK:
                _STATE["errors"] += 1
                _STATE["last_error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        for _ in range(int(interval * 2)):
            time.sleep(0.5)
            with _LOCK:
                if not _STATE["running"]:
                    break


def start(limit: int = 5, interval: float = 20.0, words: list[str] | None = None) -> dict:
    global _THREAD
    import os
    # Flask reloader spawns two processes; only run the worker in the child.
    if os.getenv("WERKZEUG_RUN_MAIN") == "true" and _THREAD is not None and _THREAD.is_alive():
        with _LOCK:
            if _STATE["running"]:
                return {"ok": False, "error": "already running", **status()}
    with _LOCK:
        if _STATE["running"]:
            return {"ok": False, "error": "already running", **status()}
        _STATE.update(running=True, last_error=None, started_at=time.time())
    _THREAD = threading.Thread(target=_run_loop, args=(limit, interval, words),
                               daemon=True)
    _THREAD.start()
    return {"ok": True, **status()}


def stop() -> dict:
    with _LOCK:
        _STATE["running"] = False
    return {"ok": True, **status()}


def status() -> dict:
    with _LOCK:
        return dict(_STATE)
