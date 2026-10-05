"""Public-launch slice: submit validation, API keys, openapi, rate fallback."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine
from bausastra import public as pub


def _mem_engine():
    eng = create_engine("sqlite:///:memory:", future=True,
                        connect_args={"check_same_thread": False})
    # minimal core tables needed by submit_word (entries/definitions/examples/sources)
    import sqlite3  # noqa
    from sqlalchemy import text as stext
    schema = (Path(__file__).resolve().parents[1] / "sql" / "schema_sqlite.sql").read_text()
    with eng.begin() as c:
        for stmt in [s.strip() for s in schema.split(";") if s.strip()]:
            c.execute(stext(stmt))
    return eng


def test_validate_submit_ok_and_honeypot():
    clean, err = pub.validate_submit({"headword": "banyu", "definition": "air",
                                      "lang": "jv", "website": "http://spam"})
    assert err == "spam detected" and clean is None
    clean, err = pub.validate_submit({"headword": "b", "definition": "x"})
    assert err is not None
    clean, err = pub.validate_submit({"headword": "banyu", "definition": "air",
                                      "speech_level": "ngoko", "pos": "n"})
    assert err is None and clean["headword"] == "banyu"


def test_validate_submit_rejects_bad_enums():
    _, err = pub.validate_submit({"headword": "banyu", "definition": "air", "lang": "xx"})
    assert err is not None
    _, err = pub.validate_submit({"headword": "banyu", "definition": "air",
                                  "speech_level": "sultan"})
    assert err is not None


def test_apikey_roundtrip_and_revoke():
    eng = _mem_engine()
    out = pub.create_key(eng, "test-app")
    assert out["raw_key"].startswith("baus_")
    ident = pub.verify_key(eng, out["raw_key"])
    assert ident and ident["name"] == "test-app"
    assert pub.verify_key(eng, "baus_bogus") is None
    assert pub.revoke_key(eng, out["prefix"]) is True
    assert pub.verify_key(eng, out["raw_key"]) is None


def test_submit_word_creates_community_draft():
    eng = _mem_engine()
    clean, _ = pub.validate_submit({"headword": "banyu", "definition": "air",
                                    "submitter": "tester"})
    out = pub.submit_word(eng, clean, "ip:127.0.0.1")
    assert out["ok"] is True
    from sqlalchemy import text as stext
    with eng.connect() as c:
        n = c.execute(stext(
            "SELECT COUNT(*) FROM definitions WHERE definition LIKE '[COMMUNITY-DRAFT%'")).scalar()
    assert n == 1


def test_openapi_has_public_paths():
    import bausastra.api as api
    spec = api._openapi_spec()
    for p in ("/healthz", "/api/v1/submit", "/api/v1/report", "/api/v1/sources",
              "/api/v1/export", "/api/v1/keys", "/api/openapi.json"):
        assert p in spec["paths"], p


def test_rate_fallback_allows_then_blocks():
    import bausastra.api as api
    # unique bucket so parallel runs don't interfere
    bucket = f"test-bucket-{id(object())}"
    assert api._rate_allow(bucket, 2, 60) is True
    assert api._rate_allow(bucket, 2, 60) is True
    assert api._rate_allow(bucket, 2, 60) is False
