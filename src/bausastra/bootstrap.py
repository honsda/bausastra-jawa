"""Bootstrap loaders: pull existing Javanese dictionaries into the DB.

Priority sources (free, no key needed):
 1. HuggingFace `junwatu/javanese-multilingual-lexicon` (202k entries, 32 sources, Sastra.org)
    -- GATED: needs `hf auth login` + accept conditions on the dataset page.
 2. GitHub `nsulistiyawan/sastra-jawa` CSVs: dictionary.csv + dasanama.csv (synonyms)
 3. Mendeley Indonesian-Javanese starter kit (ngoko/krama_alus/krama_inggil) — manual download
 4. KBJI official site (Balai Bahasa DIY, 41.2k lema) — HTML scrape, no API key needed:
    list pages /abjad/<L>?page=N + detail/search pages /kata/<word>
 5. kamusjawa.net / glosbe (HTML scrape, best-effort)

Each loader inserts into sources -> entries -> definitions (+ relations).
Idempotent: uses ON CONFLICT / INSERT OR IGNORE. Batched commits for speed.
"""
from __future__ import annotations
import csv
import io
import json
import re
import time
from pathlib import Path
import requests

from sqlalchemy import text as stext
from sqlalchemy.engine import Connection
from .javanese import normalize

ROOT = Path(__file__).resolve().parents[2]
UA = {"User-Agent": "BausastraBot/0.1 (+javanese-dictionary-research; contact: local)"}


def _is_pg(engine) -> bool:
    return engine.dialect.name == "postgresql"


def upsert_source(engine, name: str, url: str, kind="dictionary",
                  license: str | None = None, language_pair: str | None = None,
                  notes: str | None = None) -> int:
    with engine.begin() as c:
        if _is_pg(engine):
            c.execute(stext("""
                INSERT INTO sources (name, url, kind, license, language_pair, notes)
                VALUES (:n,:u,:k,:l,:lp,:nt)
                ON CONFLICT (name) DO UPDATE SET url=EXCLUDED.url, notes=EXCLUDED.notes
            """), {"n": name, "u": url, "k": kind, "l": license, "lp": language_pair, "nt": notes})
        else:
            c.execute(stext("""
                INSERT OR IGNORE INTO sources (name, url, kind, license, language_pair, notes)
                VALUES (:n,:u,:k,:l,:lp,:nt)
            """), {"n": name, "u": url, "k": kind, "l": license, "lp": language_pair, "nt": notes})
        row = c.execute(stext("SELECT id FROM sources WHERE name=:n"), {"n": name}).first()
        return int(row[0])


def _insert_fast(conn: Connection, is_pg: bool, headword: str, definition: str,
                 lang="jv", def_lang="id", pos=None, speech_level=None,
                 source_id=None, source_ref=None, aksara=None, raw=None) -> int | None:
    """Insert one entry using an EXISTING connection (caller owns the transaction)."""
    if not headword or not headword.strip():
        return None
    headword = headword.strip()[:200]
    norm = normalize(headword)
    raw_json = json.dumps(raw, ensure_ascii=False) if isinstance(raw, dict) else raw
    if is_pg:
        conn.execute(stext("""
            INSERT INTO entries (headword, headword_norm, lang, pos, speech_level,
                                 aksara_jawa, source_id, source_ref, raw)
            VALUES (:h,:n,:l,:p,:s,:a,:sid,:ref, CAST(:raw AS JSONB))
            ON CONFLICT DO NOTHING
        """), {"h": headword, "n": norm, "l": lang, "p": pos, "s": speech_level,
               "a": aksara, "sid": source_id, "ref": source_ref, "raw": raw_json})
    else:
        conn.execute(stext("""
            INSERT OR IGNORE INTO entries
            (headword, headword_norm, lang, pos, speech_level, aksara_jawa, source_id, source_ref, raw)
            VALUES (:h,:n,:l,:p,:s,:a,:sid,:ref,:raw)
        """), {"h": headword, "n": norm, "l": lang, "p": pos, "s": speech_level,
               "a": aksara, "sid": source_id, "ref": source_ref, "raw": raw_json})
    if is_pg:
        row = conn.execute(stext(
            "SELECT id FROM entries WHERE headword=:h AND lang=:l AND source_id IS NOT DISTINCT FROM :sid"),
            {"h": headword, "l": lang, "sid": source_id}).first()
    else:
        row = conn.execute(stext(
            "SELECT id FROM entries WHERE headword=:h AND lang=:l"),
            {"h": headword, "l": lang}).first()
    if row is None:
        return None
    eid = int(row[0])
    if definition and definition.strip():
        conn.execute(stext("""
            INSERT INTO definitions (entry_id, lang, definition)
            VALUES (:e,:dl,:d)
        """), {"e": eid, "dl": def_lang, "d": definition.strip()[:4000]})
    return eid


def insert_entry(engine, headword: str, definition: str, lang="jv", def_lang="id",
                 pos=None, speech_level=None, source_id=None, source_ref=None,
                 aksara=None, raw=None) -> int | None:
    """Single-entry convenience wrapper (own transaction)."""
    with engine.begin() as conn:
        return _insert_fast(conn, _is_pg(engine), headword, definition, lang, def_lang,
                            pos, speech_level, source_id, source_ref, aksara, raw)


def _flush_batch(engine, is_pg: bool, batch: list[dict]) -> int:
    n = 0
    with engine.begin() as conn:
        for item in batch:
            if _insert_fast(conn, is_pg, **item):
                n += 1
    return n


# ---- Loader 1: HuggingFace multilingual lexicon (recommended boost-start) ----
def load_huggingface(engine, pairs=("jav_en", "ind_jav", "jav_jav"), limit_per_pair=5000) -> int:
    """Load subsets of junwatu/javanese-multilingual-lexicon. Needs `datasets` + internet
    + `hf auth login` (dataset is gated: accept conditions on its HF page)."""
    try:
        from datasets import load_dataset
    except ImportError:
        print("[bootstrap] HF skipped: `pip install datasets` first.")
        return 0
    sid = upsert_source(engine, "sastra.org-multilingual-lexicon",
                        "https://huggingface.co/datasets/junwatu/javanese-multilingual-lexicon",
                        kind="dictionary", license="CC BY-NC 4.0 (research, non-commercial)",
                        language_pair="jv/kawi/id/en/nl/fr",
                        notes="202k entries from Yayasan Sastra Lestari archive")
    is_pg = _is_pg(engine)
    n = 0
    for pair in pairs:
        try:
            ds = load_dataset("junwatu/javanese-multilingual-lexicon", pair, split="train")
        except Exception as e:
            print(f"[bootstrap] HF pair {pair} FAILED: {e}")
            print("  -> if 401/gated: run `hf auth login`, accept conditions at")
            print("     https://huggingface.co/datasets/junwatu/javanese-multilingual-lexicon")
            continue
        batch: list[dict] = []
        for i, row in enumerate(ds):
            if i >= limit_per_pair:
                break
            hw = str(row.get("headword", "") or "")
            df = str(row.get("definition", "") or "")
            src = str(row.get("source", "") or "")
            langtag = str(row.get("lang", "") or "")
            # Direction: lang=="Ind" (or ind_* pairs) => headword is Indonesian, gloss is Javanese.
            if pair.startswith("ind") or langtag.strip().lower() == "ind":
                lang, def_lang = "id", "jv"
            elif pair.startswith("kawi"):
                lang, def_lang = "kawi", "id"
            else:
                lang, def_lang = "jv", "id" if pair.endswith("jav") else pair.split("_")[-1]
                if def_lang not in ("id", "en", "nl", "fr", "jv"):
                    def_lang = "id"
            batch.append({"headword": hw, "definition": df, "lang": lang, "def_lang": def_lang,
                          "source_id": sid, "source_ref": f"{pair}:{row.get('id','')}",
                          "raw": {"source": src, "langtag": langtag, "pair": pair}})
            if len(batch) >= 500:
                n += _flush_batch(engine, is_pg, batch)
                batch.clear()
                print(f"[bootstrap] HF {pair}: {n} total...")
        if batch:
            n += _flush_batch(engine, is_pg, batch)
    print(f"[bootstrap] HF done: {n} entries")
    return n


# ---- Loader 2: sastra-jawa dictionary.csv from GitHub ----
SASTRA_CSV_URL = "https://raw.githubusercontent.com/nsulistiyawan/sastra-jawa/master/csv/dictionary.csv"
DASANAMA_CSV_URL = "https://raw.githubusercontent.com/nsulistiyawan/sastra-jawa/master/csv/dasanama.csv"


def _download_text(url: str) -> str:
    r = requests.get(url, timeout=60, headers=UA)
    r.raise_for_status()
    try:
        return r.content.decode("utf-8")
    except UnicodeDecodeError:
        return r.content.decode("latin1")


def load_sastra_csv(engine, url: str = SASTRA_CSV_URL, limit: int = 20000) -> int:
    sid = upsert_source(engine, "sastra-jawa-csv",
                        "https://github.com/nsulistiyawan/sastra-jawa",
                        kind="dictionary", license="check-repo",
                        language_pair="jv->id", notes="Kamus Bahasa Jawa CSV")
    text = _download_text(url)
    reader = csv.DictReader(io.StringIO(text), skipinitialspace=True)
    # normalize headers case-insensitively: file uses Indonesia,Javanese,Alphabet
    fieldmap = {(k or "").strip().lower(): k for k in (reader.fieldnames or [])}
    jv_key = fieldmap.get("javanese") or fieldmap.get("jawa") or fieldmap.get("kata") or fieldmap.get("headword")
    id_key = fieldmap.get("indonesia") or fieldmap.get("arti") or fieldmap.get("definition")
    print(f"[bootstrap] sastra CSV columns: {reader.fieldnames}")
    is_pg = _is_pg(engine)
    batch: list[dict] = []
    n = 0
    rows = 0
    for row in reader:
        rows += 1
        if n >= limit:
            break
        hw_cell = (row.get(jv_key) if jv_key else None) or ""
        df = (row.get(id_key) if id_key else None) or ""
        if not hw_cell.strip():
            continue
        # Javanese cell often packs variants: "banyu; 2 duduh; 3 kanggo ..." or "kakang, mbokayu."
        parts = re.split(r"[;]", hw_cell)
        inserted_any = False
        for j, part in enumerate(parts):
            part = re.sub(r"^\d+[\.\)]?\s*", "", part.strip().strip("."))
            head = re.split(r"[:,(]", part)[0].strip()
            if not head or len(head) > 60:
                continue
            if re.match(r"^(kc|k|bngs|tmb|ut)\.\s", head):
                continue
            defi = df.strip() if j == 0 else f"{df.strip()} (varian: {hw_cell.strip()[:200]})"
            batch.append({"headword": head, "definition": defi, "lang": "jv",
                          "source_id": sid,
                          "raw": {"id_word": df.strip(), "jv_cell": hw_cell.strip()[:500]}})
            inserted_any = True
            if len(batch) >= 500:
                n += _flush_batch(engine, is_pg, batch)
                batch.clear()
        if not inserted_any:
            batch.append({"headword": hw_cell.strip()[:200], "definition": df.strip(),
                          "lang": "jv", "source_id": sid, "raw": dict(row)})
        if rows % 2000 == 0:
            print(f"[bootstrap] sastra CSV scanned {rows} rows, inserted ~{n}...")
    if batch:
        n += _flush_batch(engine, is_pg, batch)
    print(f"[bootstrap] sastra CSV done: {n} entries from {rows} rows")
    return n


# ---- Loader 2b: dasanama.csv (synonyms -> entries + relations) ----
def load_dasanama(engine, url: str = DASANAMA_CSV_URL, limit: int = 10000) -> int:
    """Dasanama = Javanese synonym lists. Inserts each synonym as entry + synonym relations."""
    sid = upsert_source(engine, "sastra-jawa-dasanama",
                        "https://github.com/nsulistiyawan/sastra-jawa",
                        kind="dictionary", license="check-repo",
                        language_pair="jv->jv", notes="Dasanama synonym lists")
    text = _download_text(url)
    reader = csv.DictReader(io.StringIO(text), skipinitialspace=True)
    fieldmap = {(k or "").strip().lower(): k for k in (reader.fieldnames or [])}
    hw_key = fieldmap.get("tembung") or fieldmap.get("kata") or fieldmap.get("headword")
    syn_key = fieldmap.get("dasanama") or fieldmap.get("sinonim") or fieldmap.get("synonyms")
    print(f"[bootstrap] dasanama columns: {reader.fieldnames}")
    is_pg = _is_pg(engine)
    n_rel = 0
    n = 0
    with engine.begin() as conn:
        for i, row in enumerate(reader):
            if i >= limit:
                break
            hw = ((row.get(hw_key) or "") if hw_key else "").strip()
            syns = ((row.get(syn_key) or "") if syn_key else "")
            if not hw:
                continue
            main_id = _insert_fast(conn, is_pg, hw, f"dasanama: {syns.strip()[:300]}",
                                   lang="jv", def_lang="jv", source_id=sid,
                                   raw={"dasanama": syns.strip()[:1000]})
            if main_id:
                n += 1
            syn_list = [s.strip(' ."\'“”') for s in re.split(r"[,;]", syns) if s.strip(' ."\'“”')]
            for s in syn_list[:20]:
                if len(s) > 60 or not s:
                    continue
                sid2 = _insert_fast(conn, is_pg, s, f"sinonim {hw}", lang="jv",
                                    def_lang="jv", source_id=sid)
                if sid2 and main_id and sid2 != main_id:
                    if is_pg:
                        conn.execute(stext(
                            "INSERT INTO relations (from_entry_id, to_entry_id, rel_type)"
                            " VALUES (:a,:b,'synonym') ON CONFLICT DO NOTHING"),
                            {"a": main_id, "b": sid2})
                    else:
                        conn.execute(stext(
                            "INSERT OR IGNORE INTO relations (from_entry_id, to_entry_id, rel_type)"
                            " VALUES (:a,:b,'synonym')"),
                            {"a": main_id, "b": sid2})
                    n_rel += 1
    print(f"[bootstrap] dasanama done: {n} headwords, {n_rel} synonym relations")
    return n


# ---- Loader 4: KBJI official site (no API key needed, HTML scrape) ----
KBJI_BASE = "https://kbji.kemendikdasmen.go.id"


def kbji_parse_search(html: str) -> list[tuple[str, str]]:
    """Parse a /kata/<word> page: returns [(headword, definition)] incl. sub-entries."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[str, str]] = []
    for p in soup.select("p.indent-text span.kbji-entry-copy"):
        b = p.find("b")
        if not b:
            continue
        head = b.get_text(" ", strip=True)
        full = p.get_text(" ", strip=True)
        defi = full[len(head):].strip(" ;-–—:")
        # skip bare cross-ref headers without gloss in this card; keep real ones
        if head and defi:
            out.append((head, defi))
    for p in soup.select("p.indent"):
        b = p.find("b")
        if not b:
            continue
        head = b.get_text(" ", strip=True)
        full = p.get_text(" ", strip=True)
        defi = full[len(head):].strip(" ;-–—:")
        if head and defi:
            out.append((head, defi))
    return out


def kbji_list_letter(letter: str, page: int = 1) -> tuple[list[str], int]:
    """Fetch /abjad/<L>?page=N. Returns (lemma_slugs, total_pages)."""
    from bs4 import BeautifulSoup
    letter = letter.upper()
    url = f"{KBJI_BASE}/abjad/{letter}" + (f"?page={page}" if page > 1 else "")
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    slugs: list[str] = []
    for a in soup.select('a[href^="/kata/"]'):
        href = a.get("href", "")
        slug = href.split("/kata/", 1)[-1]
        if slug and slug not in slugs:
            slugs.append(slug)
    total = page
    for a in soup.select("a"):
        t = (a.get_text(strip=True) or "")
        if t.isdigit():
            try:
                total = max(total, int(t))
            except ValueError:
                pass
    return slugs, total


def load_kbji_words(engine, words: list[str], delay: float = 0.4) -> int:
    """Targeted import: fetch /kata/<word> for each word, store all parsed pairs."""
    sid = upsert_source(engine, "kbji-official",
                        KBJI_BASE, kind="dictionary",
                        license="Balai Bahasa DIY (scraped, fair-use research)",
                        language_pair="jv->id",
                        notes="Kamus Bahasa Jawa-Indonesia digital, 41.2k lema")
    is_pg = _is_pg(engine)
    batch: list[dict] = []
    n = 0
    for i, w in enumerate(words):
        slug = requests.utils.quote(w.strip(), safe="")
        try:
            r = requests.get(f"{KBJI_BASE}/kata/{slug}", headers=UA, timeout=30)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            pairs = kbji_parse_search(r.text)
        except Exception as e:
            print(f"[kbji] {w}: fetch failed ({e})")
            continue
        for head, defi in pairs:
            batch.append({"headword": head, "definition": defi, "lang": "jv",
                          "def_lang": "id", "source_id": sid,
                          "source_ref": f"/kata/{slug}"})
        if len(batch) >= 300:
            n += _flush_batch(engine, is_pg, batch)
            batch.clear()
        if (i + 1) % 20 == 0:
            print(f"[kbji] {i + 1}/{len(words)} words, ~{n} entries...")
        time.sleep(delay)
    if batch:
        n += _flush_batch(engine, is_pg, batch)
    print(f"[kbji] words done: {n} entries from {len(words)} lookups")
    return n


def load_kbji_letters(engine, letters: str = "A", pages_per_letter: int = 1,
                      delay: float = 0.4, max_words: int = 500) -> int:
    """Bulk import: enumerate list pages, then fetch each lemma page."""
    slugs: list[str] = []
    for ch in letters.upper():
        if not ch.isalpha():
            continue
        try:
            first, total = kbji_list_letter(ch, 1)
        except Exception as e:
            print(f"[kbji] letter {ch}: list failed ({e})")
            continue
        pages = min(total, pages_per_letter)
        print(f"[kbji] letter {ch}: {total} pages, scraping {pages}...")
        slugs.extend(first)
        for p in range(2, pages + 1):
            try:
                s, _ = kbji_list_letter(ch, p)
                slugs.extend(s)
            except Exception as e:
                print(f"[kbji] letter {ch} page {p} failed ({e})")
            time.sleep(delay)
            if len(slugs) >= max_words:
                break
        if len(slugs) >= max_words:
            break
    # dedupe, preserve order, decode URL-encoding for lookup
    seen: set[str] = set()
    words: list[str] = []
    for s in slugs:
        if s not in seen:
            seen.add(s)
            words.append(s)
        if len(words) >= max_words:
            break
    print(f"[kbji] {len(words)} lemma slugs collected, fetching detail pages...")
    import urllib.parse
    return load_kbji_words(engine, [urllib.parse.unquote(w) for w in words], delay=delay)


# ---- Loader 3: seed words (always works offline) ----
SEED = [
    ("sugeng", "selamat", "jv", None),
    ("rawuh", "datang / kehadiran", "jv", None),
    ("matur", "berterima kasih / mengucapkan", "jv", "krama_inggil"),
    ("nuwun", "terima kasih", "jv", "krama_inggil"),
    ("piye", "bagaimana", "jv", "ngoko"),
    ("kepriye", "bagaimana", "jv", "krama_madya"),
    ("kados_pundi", "bagaimana", "jv", "krama_inggil"),
    ("aku", "saya", "jv", "ngoko"),
    ("kulo", "saya", "jv", "krama_alus"),
    ("kowe", "kamu", "jv", "ngoko"),
    ("sampeyan", "kamu/anda", "jv", "krama_madya"),
    ("panjenengan", "anda", "jv", "krama_inggil"),
    ("basa", "bahasa", "jv", None),
    ("tembung", "kata", "jv", None),
    ("tegese", "artinya", "jv", None),
    ("lan", "dan", "jv", None),
    ("karo", "dengan / dan (ngoko)", "jv", "ngoko"),
    ("kalian", "dengan", "jv", "krama_madya"),
    ("saka", "dari", "jv", None),
    ("saking", "dari", "jv", "krama_alus"),
    ("ing", "di / pada", "jv", None),
    ("sing", "yang", "jv", None),
    ("ora", "tidak", "jv", "ngoko"),
    ("mboten", "tidak", "jv", "krama_alus"),
    ("ya", "ya", "jv", None),
    ("nggih", "ya (sopan)", "jv", "krama_alus"),
    ("monggo", "silakan", "jv", "krama_alus"),
    ("sugeng_rawuh", "selamat datang", "jv", None),
    ("sugeng_enjing", "selamat pagi", "jv", None),
    ("mangan", "makan (ngoko)", "jv", "ngoko"),
    ("nedha", "makan (krama)", "jv", "krama_inggil"),
    ("ngombe", "minum (ngoko)", "jv", "ngoko"),
    ("ngunjuk", "minum (krama)", "jv", "krama_inggil"),
    ("turu", "tidur (ngoko)", "jv", "ngoko"),
    ("sare", "tidur (krama)", "jv", "krama_inggil"),
]


def load_seed(engine) -> int:
    sid = upsert_source(engine, "bausastra-seed", "", kind="manual",
                        language_pair="jv->id", notes="curated starter words incl. speech levels")
    batch = [{"headword": hw.replace("_", " "), "definition": df, "lang": lang,
              "speech_level": level, "source_id": sid}
             for hw, df, lang, level in SEED]
    n = _flush_batch(engine, _is_pg(engine), batch)
    print(f"[bootstrap] seed done: {n}")
    return n
