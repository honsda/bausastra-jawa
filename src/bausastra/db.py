"""DB layer: Postgres via SQLAlchemy, with SQLite fallback for offline dev.

Uses DATABASE_URL from .env. If Postgres is unreachable, falls back to
data/bausastra.db (SQLite) so the scraper still works on a fresh machine.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from dotenv import load_dotenv

log = logging.getLogger(__name__)

load_dotenv()
ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PG = ROOT / "sql" / "schema.sql"
SCHEMA_SQLITE = ROOT / "sql" / "schema_sqlite.sql"


def database_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://bausastra:bausastra_dev@localhost:5432/bausastra")


def is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


def get_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    try:
        eng = create_engine(url, pool_pre_ping=True, future=True,
                            connect_args={"connect_timeout": 3})
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        return eng
    except Exception as e:
        # Fallback to SQLite so work can continue without a server
        log.warning("Postgres unreachable (%s); falling back to SQLite.", e)
        print(f"[db] Postgres unreachable ({e}); falling back to SQLite.")
        sqlite_path = ROOT / "data" / "bausastra.db"
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(f"sqlite:///{sqlite_path}", future=True,
                            connect_args={"check_same_thread": False})
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        # init sqlite schema lazily (idempotent: IF NOT EXISTS)
        init_sqlite(eng)
        return eng


def init_postgres(engine: Engine) -> None:
    sql = SCHEMA_PG.read_text(encoding="utf-8")
    # psycopg cannot execute multi-statement strings with parameters in one
    # cursor.execute reliably when CREATE OR REPLACE VIEW etc. are mixed,
    # so split on semicolons at statement boundaries.
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for stmt in statements:
            cur.execute(stmt)
        raw.commit()
    finally:
        raw.close()


def init_sqlite(engine: Engine) -> None:
    if not SCHEMA_SQLITE.exists():
        return
    import sqlite3
    path = str(engine.url.database)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_SQLITE.read_text(encoding="utf-8"))
    con.close()
