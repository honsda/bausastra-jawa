# Bausastra — Javanese Dictionary Mapping Workspace

`Bausastra` = Javanese for "dictionary". Goal: map out the Javanese lexicon
(ngoko / krama madya / krama inggil / kawi + Indonesian/English glosses) in
Postgres, and actively discover words from Javanese sites with a Python scraper.

## 1. Prereqs

- Python 3.11+
- Postgres 14+ — pick one:
  - **Docker (recommended):** `docker compose up -d`
  - **Native Windows:** `winget install PostgreSQL.PostgreSQL.18`, then create db `bausastra`
- No Postgres yet? The Python tool **falls back to SQLite** (`data/bausastra.db`) so you can work immediately.

## 2. Quick start (Windows PowerShell)

```powershell
cd C:\Users\asus\Desktop\honsda\bausastra
copy .env.example .env
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .

# init schema (Postgres if reachable, else SQLite)
python -m bausastra.cli initdb

# boost-start: curated seed (offline, always works)
python -c "from bausastra.db import get_engine; from bausastra import bootstrap as b; e=get_engine(); b.load_seed(e)"

# full boost-start: seed + full CSV + dasanama + HuggingFace lexicon (needs internet)
python -m bausastra.cli bootstrap --limit 5000 --csv-limit 20000

# KBJI official (no API key needed — polite HTML scrape, resumable):
python -m bausastra.cli kbji --words "wonten,kang,wong"          # targeted lookup
python -m bausastra.cli kbji --unknown-top 40                     # feed scraper's unknowns to KBJI
python -m bausastra.cli kbji --letters ABC --pages 2 --max-words 300  # bulk A-Z enumeration

# lookup
python -m bausastra.cli lookup --q sugeng

# identify words in a snippet
python -m bausastra.cli identify --text "sugeng rawuh, piye kabare?"

# scrape Javanese sites (saves crawl + token stats to DB)
python -m bausastra.cli scrape --url https://jv.wikipedia.org/wiki/Basa_Jawa
python -m bausastra.cli scrape --file data/urls.txt --limit 5
```

## 3. Postgres with Docker

```powershell
copy .env.example .env
docker compose up -d
docker compose ps
# connection: postgresql://bausastra:bausastra_dev@localhost:5432/bausastra
# Adminer UI: http://localhost:8080 (server=db, user/pass from .env)
psql $env:DATABASE_URL -f sql/schema.sql  # or: python -m bausastra.cli initdb
```

Native Postgres (no Docker):

```powershell
winget install PostgreSQL.PostgreSQL.18
# during install note the postgres superuser password, then:
createdb -U postgres bausastra
$env:DATABASE_URL="postgresql://postgres:<pw>@localhost:5432/bausastra"
python -m bausastra.cli initdb
```

## 4. Schema

See `sql/schema.sql` (Postgres) and `sql/schema_sqlite.sql` (fallback):

- `sources` — provenance of every import (HF lexicon, sastra-jawa CSV, KBJI, crawls…)
- `entries` — headword + lang (`jv/kawi/id/en/nl/fr`) + `speech_level` + `aksara_jawa`
- `definitions` — senses / translations
- `examples`, `relations` (dasanama/synonyms, ngoko↔krama)
- `crawl_pages` + `token_observations` — what the scraper saw in the wild
- views: `v_dictionary`, `v_unknown_candidates` (new words to add!)

## 5. Bootstrap sources (existing dictionaries — free)

| Source | Size | Status / how to load |
|---|---|---|
| `junwatu/javanese-multilingual-lexicon` (HF, from Sastra.org, 32 dicts 1830–2010) | 202,912 entries (jv↔en/nl/fr/id, kawi…) | **GATED** — `hf auth login` + accept conditions on the HF page, then `bootstrap --limit N` |
| `nsulistiyawan/sastra-jawa` dictionary.csv | 11,095 rows → ~16k entries | ✅ imported (`--csv-limit 20000`) |
| `nsulistiyawan/sastra-jawa` dasanama.csv | 428 headwords, 2,440 synonym relations | ✅ imported (`--dasanama`) |
| KBJI official (Balai Bahasa DIY, 41.2k lema) | scraped via `/kata/<word>` + `/abjad/<L>` | ✅ pipeline ready (`kbji` command); ~5.9k entries so far |
| Mendeley Indonesian-Javanese starter kit (ngoko/krama_alus/krama_inggil JSON) | ~thousands | manual download → adapt `bootstrap.py` |
| kamusjawa.net, Glosbe jv/en, sastra.org | large | `scrape --url` |

Current DB: **~19k entries, 2.4k relations**. Basa_Jawa benchmark: known words **44 → 160** after KBJI pass.
Recommended loop: **scrape → `--unknown-top N` → kbji → rescrape**; then full A–Z bulk (`--letters ABCDEFGHIJKLMNOPQRSTUVWXYZ --pages 53`) for all 41.2k KBJI lemmata (hours, resumable).

## 6. Scraper: how word identification works

1. `fetch_html` — requests + trafilatura/BeautifulSoup → clean text.
2. `javanese_score` — Javanese stopwords (`lan, kang, ing, sing, karo, saka…`) vs Indonesian stopwords + ꦲꦏ꧀ꦱꦫ Jawa script detection → 0..1 + `lang_guess` (`jv/mixed/id/other`).
3. `analyze_text` — tokenize, normalize (lowercase + strip accents), compare against `entries.headword_norm` → `known` vs `unknown` + frequencies.
4. Persist to `crawl_pages` + `token_observations`; query `v_unknown_candidates` for new dictionary headwords.

## 7. Project layout

```
docker-compose.yml  postgres:18 + adminer
.env.example
sql/schema.sql / schema_sqlite.sql
src/bausastra/  db.py javanese.py scraper.py bootstrap.py cli.py
data/urls.txt   starter Javanese sites
```

## 9. Website (Svelte + JSON API)

`web/` is a Svelte + Vite app (search, A–Z letter categories, word detail).
`src/bausastra/api.py` is the JSON API backend (`/api/search`, `/api/letters`,
`/api/letter/<A-Z>`, `/api/word/<id>`, `/api/stats`).

```powershell
# easiest: double-click run.bat (builds frontend if needed, starts server, opens browser)
# or manually:
python -m bausastra.cli serve --port 5000

# frontend dev (needs backend running; Vite proxies /api → :5000)
cd web; npm install; npm run dev   # http://127.0.0.1:5173

# production build (served by the backend same-origin, no CORS needed)
cd web; npm run build
```

## 10. Public API (for other apps, e.g. sinau)

Stable versioned endpoints + machine-readable docs at `/api/docs`.
Transliteration uses the **sinau tables** (`src/bausastra/sinau_lexicon.json`,
ported from `honsda/sinau`); every `jv`/`kawi` entry also stores `aksara_jawa`.

```powershell
python -m bausastra.cli transliterate --text "sugeng rawuh"  # CLI
python -m bausastra.cli transliterate --backfill              # fill DB aksara
curl "http://127.0.0.1:5000/api/v1/transliterate?text=sugeng%20rawuh"
curl "http://127.0.0.1:5000/api/v1/reverse?text=ꦧꦚꦸ"
curl "http://127.0.0.1:5000/api/v1/search?q=wonten"
curl "http://127.0.0.1:5000/api/v1/word/5184"   # definitions + aksara_jawa + synonyms
```

| Endpoint | Desc |
|---|---|
| `GET /api/v1/search?q=&limit=` | ranked search (exact → prefix → contains) |
| `GET /api/v1/letter/<A-Z>?page=` | browse by initial, 50/page |
| `GET /api/v1/letters` | categories with counts |
| `GET /api/v1/word/<id>` | detail + `aksara_jawa` + synonyms |
| `GET\|POST /api/v1/transliterate` | Latin → Aksara (`?text=` or JSON) |
| `GET\|POST /api/v1/reverse` | Aksara → Latin |
| `GET /api/v1/stats`, `GET /api/docs` | counts, full endpoint listing |

## 11. Next steps

## 10. Next steps

- [ ] Get KBJI API key (Balai Bahasa DIY) for 41k official lemmata bulk import
- [ ] Add `aksara_jawa` transliteration (CarakanJS / latin→javanese lib)
- [ ] Add FastAPI `GET /lookup?q=` on top of Postgres trigram index
- [ ] Cron crawl of jv.wikipedia + sastra.org → review `v_unknown_candidates` weekly
