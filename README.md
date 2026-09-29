# Bausastra — Javanese Dictionary Mapping Workspace

`Bausastra` (Javanese for "dictionary") is a workspace for mapping the Javanese
lexicon — **ngoko / krama madya / krama inggil / kawi**, plus Indonesian/English
glosses and **Aksara Jawa** — backed by Postgres (with SQLite fallback), a
Python scraper/bootstrap pipeline, a Flask JSON API, and a Svelte web UI.

## Features

- **Dictionary DB** in Postgres: headwords, senses, examples, relations
  (dasanama/synonyms, ngoko↔krama), with trigram search indexes.
- **Bootstrap importers**: curated seed, sastra-jawa CSV + dasanama, KBJI
  official site (41.2k lemmata, polite HTML scrape), HuggingFace
  multilingual lexicon (202k entries, gated).
- **Scraper + word identifier**: fetch Javanese sites, score Javanese vs
  Indonesian, split tokens into known vs unknown, persist crawl + token stats.
- **Transliteration**: Latin ↔ Aksara Jawa via `sinau` tables, on read,
  on import, and via CLI/API, with DB backfill.
- **Web UI + public API**: Svelte + Vite frontend served by the Flask backend
  same-origin; versioned `/api/v1/*` endpoints with `/api/docs`.
- **SQLite fallback**: works immediately with `data/bausastra.db` when
  Postgres is unreachable.

## Tech stack

| Layer | Tech |
|---|---|
| DB | Postgres 18 (Docker) / Postgres 14+ native, SQLite fallback |
| Backend | Python 3.11+, SQLAlchemy 2, Click, Flask, psycopg |
| Scraping / NLP | requests, BeautifulSoup4, lxml, trafilatura, pandas |
| Lexicon imports | datasets, huggingface-hub, tqdm, python-dotenv |
| Frontend | Svelte 4, Vite 5, carakanjs |
| Ops | docker-compose (postgres + adminer), `run.bat` launcher |

## Project layout

```
.
├── docker-compose.yml      # postgres:18 + adminer
├── .env.example            # DATABASE_URL + postgres settings
├── run.bat                 # one-click: build web/ + start API + open browser
├── sql/
│   ├── schema.sql          # Postgres schema + pg_trgm views
│   └── schema_sqlite.sql   # SQLite fallback schema
├── src/bausastra/
│   ├── db.py               # get_engine() + Postgres/SQLite init
│   ├── javanese.py         # normalize, stopwords, javanese_score
│   ├── scraper.py          # fetch_html, analyze_text, scrape_url
│   ├── bootstrap.py        # seed, sastra CSV, dasanama, KBJI, HF loaders
│   ├── transliterate.py    # Latin <-> Aksara via sinau_lexicon.json
│   ├── api.py              # Flask app: /api/* + /api/v1/* + serve web/dist
│   └── cli.py              # python -m bausastra.cli <command>
├── data/
│   ├── urls.txt            # starter Javanese sites
│   └── bausastra.db        # SQLite fallback (gitignored)
└── web/                    # Svelte + Vite app (search, A-Z, word detail)
    ├── src/
    ├── vite.config.js      # dev proxy /api -> :5000
    └── dist/               # production build, served by Flask (gitignored)
```

## Prerequisites

- Python 3.11+
- Node.js + npm (only for the `web/` frontend)
- Postgres 14+ — pick one:
  - **Docker (recommended):** `docker compose up -d`
  - **Native Windows:** `winget install PostgreSQL.PostgreSQL.18`, then create db `bausastra`
- No Postgres yet? The tool **falls back to SQLite** (`data/bausastra.db`)
  automatically, so you can start without a server.

## Quick start (Windows PowerShell)

```powershell
cd C:\Users\asus\Desktop\honsda\bausastra
copy .env.example .env
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .

# init schema (Postgres if reachable, else SQLite)
python -m bausastra.cli initdb

# offline seed (always works, no internet needed)
python -c "from bausastra.db import get_engine; from bausastra import bootstrap as b; e=get_engine(); b.load_seed(e)"

# full bootstrap: seed + sastra CSV + dasanama + HuggingFace (needs internet;
# HF dataset is gated: `hf auth login` + accept conditions on its HF page)
python -m bausastra.cli bootstrap --limit 5000 --csv-limit 20000
```

Then start the website:

```powershell
# easiest: double-click run.bat (builds frontend if needed, starts server, opens browser)
# or manually:
python -m bausastra.cli serve --port 5000  # http://127.0.0.1:5000
```

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

```dotenv
POSTGRES_USER=bausastra
POSTGRES_PASSWORD=bausastra_dev
POSTGRES_DB=bausastra
POSTGRES_PORT=5432
DATABASE_URL=postgresql://bausastra:bausastra_dev@localhost:5432/bausastra
```

`src/bausastra/db.py` reads `DATABASE_URL`. If Postgres is unreachable it
prints a warning and falls back to `data/bausastra.db` (SQLite), creating the
schema lazily. `initdb` applies `sql/schema.sql` on Postgres.

Postgres via Docker:

```powershell
copy .env.example .env
docker compose up -d
docker compose ps
# connection: postgresql://bausastra:bausastra_dev@localhost:5432/bausastra
# Adminer UI: http://localhost:8080 (server=db, user/pass from .env)
python -m bausastra.cli initdb   # or: psql $env:DATABASE_URL -f sql/schema.sql
```

Native Postgres (no Docker):

```powershell
winget install PostgreSQL.PostgreSQL.18
# note the postgres superuser password during install, then:
createdb -U postgres bausastra
$env:DATABASE_URL="postgresql://postgres:<pw>@localhost:5432/bausastra"
python -m bausastra.cli initdb
```

## Database schema

See `sql/schema.sql` (Postgres) and `sql/schema_sqlite.sql` (fallback):

- `sources` — provenance of every import (HF lexicon, sastra-jawa CSV, KBJI, crawls…)
- `entries` — headword + `headword_norm` + `lang` (`jv/kawi/id/en/nl/fr`) + `speech_level` + `aksara_jawa`
- `definitions` — one entry, many senses/translations
- `examples` — Javanese sample sentences + translations
- `relations` — dasanama/synonyms, antonyms, ngoko↔krama, variants
- `crawl_pages` + `token_observations` — what the scraper saw in the wild
- Views: `v_dictionary` (entries + aggregated definitions), `v_unknown_candidates` (new headword candidates)

Postgres adds `pg_trgm` + `unaccent` indexes on `headword_norm` and
`definitions.definition` for fuzzy/prefix search.

## Bootstrap sources

| Source | Size | How to load |
|---|---|---|
| `junwatu/javanese-multilingual-lexicon` (HF, from Sastra.org, 32 dicts 1830–2010) | 202,912 entries (jv↔en/nl/fr/id, kawi…) | **GATED** — `hf auth login` + accept conditions on HF page, then `bootstrap --limit N --pairs jav_en,ind_jav,jav_jav` |
| `nsulistiyawan/sastra-jawa` dictionary.csv | ~11k rows → ~16k entries | `bootstrap --csv --csv-limit 20000` (on by default) |
| `nsulistiyawan/sastra-jawa` dasanama.csv | 428 headwords, ~2.4k synonym relations | `bootstrap --dasanama` (on by default) |
| KBJI official (Balai Bahasa DIY, 41.2k lema) | scraped via `/kata/<word>` + `/abjad/<L>` | `kbji` command (see below); resumable, no key needed |
| kamusjawa.net, Glosbe jv/en, sastra.org | large | `scrape --url ...` |

Recommended enrichment loop: **scrape → `--unknown-top N` → kbji → rescrape**,
then full A–Z bulk for all KBJI lemmata (takes hours, resumable):

```powershell
python -m bausastra.cli kbji --words "wonten,kang,wong"                    # targeted lookup
python -m bausastra.cli kbji --unknown-top 40                               # feed scraper unknowns to KBJI
python -m bausastra.cli kbji --letters ABC --pages 2 --max-words 300        # bulk A-Z enumeration
# full KBJI: python -m bausastra.cli kbji --letters ABCDEFGHIJKLMNOPQRSTUVWXYZ --pages 53
```

## Scraper: how word identification works

1. `fetch_html` — requests + trafilatura/BeautifulSoup → clean text.
2. `javanese_score` — Javanese stopwords (`lan, kang, ing, sing, karo, saka…`)
   vs Indonesian stopwords + ꦲꦏ꧀ꦱꦫ Jawa script detection → 0..1 score +
   `lang_guess` (`jv/mixed/id/other`).
3. `analyze_text` — tokenize, normalize (lowercase + strip accents), compare
   against `entries.headword_norm` → `known` vs `unknown` + frequencies.
4. Persist to `crawl_pages` + `token_observations`; query
   `v_unknown_candidates` for new dictionary headwords.

```powershell
# identify words in a snippet (no DB writes)
python -m bausastra.cli identify --text "sugeng rawuh, piye kabare?"

# lookup (prefix search)
python -m bausastra.cli lookup --q sugeng

# scrape Javanese sites (saves crawl + token stats to DB)
python -m bausastra.cli scrape --url https://jv.wikipedia.org/wiki/Basa_Jawa
python -m bausastra.cli scrape --file data/urls.txt --limit 5
```

## CLI reference

All commands run as `python -m bausastra.cli <command>` from the repo root
with the venv active (`src/bausastra/cli.py`):

| Command | Purpose |
|---|---|
| `initdb` | Create schema (Postgres if reachable, else SQLite) |
| `bootstrap --limit N --csv-limit N [--pairs ...] [--csv/--no-csv] [--dasanama/--no-dasanama]` | Seed + HF lexicon + sastra CSV + dasanama |
| `kbji --words a,b \| --unknown-top N \| --letters ABC --pages N --max-words N` | Import from KBJI official site |
| `scrape --url U \| --file F --limit N [--save/--no-save]` | Scrape site(s), identify known/unknown |
| `identify --text "..."` | Analyze a pasted snippet (no writes) |
| `lookup --q WORD [--limit N]` | Prefix lookup in local dictionary |
| `transliterate --text ... \| --reverse ... \| --backfill` | Latin↔Aksara, or fill DB `aksara_jawa` |
| `serve --port 5000` | Start JSON API + serve built frontend |

## Website (Svelte + JSON API)

`web/` is a Svelte + Vite app (search, A–Z letter categories, word detail).
`src/bausastra/api.py` is the backend (`/api/search`, `/api/letters`,
`/api/letter/<A-Z>`, `/api/word/<id>`, `/api/stats`, plus versioned `/api/v1/*`).

```powershell
# production (served by Flask same-origin, no CORS needed)
cd web; npm install; npm run build
python -m bausastra.cli serve --port 5000   # http://127.0.0.1:5000

# frontend dev (needs backend running; Vite proxies /api -> :5000)
cd web; npm install; npm run dev            # http://127.0.0.1:5173
```

## Public API (for other apps, e.g. sinau)

Stable versioned endpoints plus machine-readable docs at `/api/docs`.
Every `jv`/`kawi` entry also carries `aksara_jawa` (auto-transliterated on
read via the **sinau tables** in `src/bausastra/sinau_lexicon.json` if missing
in the DB).

```powershell
python -m bausastra.cli transliterate --text "sugeng rawuh"  # CLI
python -m bausastra.cli transliterate --backfill              # fill DB aksara
curl "http://127.0.0.1:5000/api/v1/transliterate?text=sugeng%20rawuh"
curl "http://127.0.0.1:5000/api/v1/reverse?text=ꦧꦚꦸ"
curl "http://127.0.0.1:5000/api/v1/search?q=wonten"
curl "http://127.0.0.1:5000/api/v1/word/5184"   # definitions + aksara_jawa + synonyms
```

| Endpoint | Description |
|---|---|
| `GET /api/v1/search?q=&limit=` | Ranked search (exact → prefix → contains) |
| `GET /api/v1/letter/<A-Z>?page=` | Browse by initial, 50/page |
| `GET /api/v1/letters` | Initial-letter categories with counts |
| `GET /api/v1/word/<id>` | Detail + `aksara_jawa` + synonyms |
| `GET\|POST /api/v1/transliterate` | Latin → Aksara (`?text=` or JSON `{"text": ...}`) |
| `GET\|POST /api/v1/reverse` | Aksara → Latin (`?text=` or JSON `{"text": ...}`) |
| `GET /api/v1/stats`, `GET /api/docs` | Counts, full endpoint listing |

Unversioned `/api/*` aliases (same handlers, bare JSON) are kept for the
bundled frontend; prefer `/api/v1/*` (`{"ok": true, "data": ...}`) for new clients.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[db] Postgres unreachable … falling back to SQLite` | Expected without a server; `docker compose up -d` + `initdb` to use Postgres |
| `HF load failed (gated dataset …)` | Run `hf auth login` and accept the dataset conditions on huggingface.co, then retry `bootstrap` |
| `CSV load failed` | Check internet access to raw.githubusercontent.com; offline `load_seed` still works |
| Frontend shows `API running. Build the frontend…` | Run `cd web; npm install; npm run build`, then restart `serve` |
| `run.bat` exits with "No virtualenv" | `python -m venv .venv` + `pip install -r requirements.txt` first |
| Port 5000 busy | `python -m bausastra.cli serve --port 5001` |

## License / attribution

Dictionary content belongs to its original compilers — Sastra.org contributors,
sastra-jawa, KBJI/Balai Bahasa DIY, and the HuggingFace lexicon sources.
Use each source per its own license/terms. Code in this repo is the workspace
tooling (scraper, importers, API, web UI) around those sources.
