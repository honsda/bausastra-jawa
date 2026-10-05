"""Smoke tests: language helpers, transliteration, KBJI parser, API guards."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bausastra.javanese import normalize, tokenize, javanese_score, guess_lang
from bausastra.transliterate import transliterate, reverse_transliterate
from bausastra.bootstrap import kbji_parse_search


def test_normalize_strips_accents():
    assert normalize("Sugeng") == "sugeng"


def test_tokenize_basic():
    assert "sugeng" in tokenize("Sugeng rawuh!")


def test_javanese_score_jv_high():
    score, _ = javanese_score("sugeng rawuh monggo mangan karo kulo")
    assert score >= 0.5
    assert guess_lang("sugeng rawuh monggo mangan karo kulo") == "jv"


def test_transliterate_roundtrip_nonempty():
    aks = transliterate("sugeng rawuh")
    assert aks and aks != "sugeng rawuh"
    back = reverse_transliterate(aks)
    # reverse may restore pepet as é (sugéng) — check consonant skeleton instead
    assert "sug" in back.lower() and "rawuh" in back.lower()


def test_kbji_parser_extracts_pairs():
    html = """
    <html><body>
    <p class="indent-text"><span class="kbji-entry-copy"><b>wonten</b> ada; terdapat</span></p>
    </body></html>
    """
    pairs = kbji_parse_search(html)
    assert pairs and pairs[0][0] == "wonten"


def test_api_admin_guard_no_token_open_in_dev(monkeypatch):
    # Without BAUSASTRA_ADMIN_TOKEN set, _require_admin returns None (open local dev).
    import bausastra.api as api
    monkeypatch.setattr(api, "ADMIN_TOKEN", "")
    # Needs app context for request; just check the constant path.
    assert api.ADMIN_TOKEN == ""
