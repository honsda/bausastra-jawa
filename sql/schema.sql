-- Bausastra: Javanese dictionary mapping workspace
-- Postgres 14+ compatible. UTF-8 required for Aksara Jawa (ꦧꦱꦗꦮ).
-- Run: psql $DATABASE_URL -f sql/schema.sql

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- 1. Sources: where each entry came from (for bootstrap provenance)
CREATE TABLE IF NOT EXISTS sources (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    url TEXT,
    kind TEXT NOT NULL DEFAULT 'dictionary', -- dictionary | website | crawl | manual | api
    license TEXT,
    language_pair TEXT, -- e.g. jv->id, jv->en, kawi->id
    fetched_at TIMESTAMPTZ DEFAULT now(),
    notes TEXT
);

-- 2. Entries: headwords
CREATE TABLE IF NOT EXISTS entries (
    id SERIAL PRIMARY KEY,
    headword TEXT NOT NULL,
    headword_norm TEXT NOT NULL, -- lowercased, unaccented for search
    lang TEXT NOT NULL DEFAULT 'jv', -- jv | kawi | id | en | nl | fr
    pos TEXT, -- n, v, a, adv, pron, ...
    speech_level TEXT, -- ngoko | krama_madya | krama_inggil | krama_alus | kawi | indonesia
    aksara_jawa TEXT, -- ꦧꦻꦴꦱꦱ꧀ꦠꦿ
    pronunciation TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    source_ref TEXT, -- original id / catalog number, e.g. sastra.org ti_id
    raw JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_entries_norm ON entries USING gin (headword_norm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_entries_headword ON entries (headword);
CREATE INDEX IF NOT EXISTS idx_entries_lang ON entries (lang);
CREATE INDEX IF NOT EXISTS idx_entries_level ON entries (speech_level);
CREATE UNIQUE INDEX IF NOT EXISTS uq_entries_headword_lang_source ON entries (headword, lang, COALESCE(source_id, -1));

-- 3. Definitions: one entry can have many senses / translations
CREATE TABLE IF NOT EXISTS definitions (
    id SERIAL PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    lang TEXT NOT NULL DEFAULT 'id', -- definition language
    definition TEXT NOT NULL,
    context TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_definitions_entry ON definitions (entry_id);
CREATE INDEX IF NOT EXISTS idx_definitions_text ON definitions USING gin (definition gin_trgm_ops);

-- 4. Examples
CREATE TABLE IF NOT EXISTS examples (
    id SERIAL PRIMARY KEY,
    entry_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
    javanese_text TEXT,
    translation_text TEXT,
    translation_lang TEXT DEFAULT 'id',
    source_url TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 5. Relations: dasanama (synonyms), antonyms, variants, krama mappings
CREATE TABLE IF NOT EXISTS relations (
    id SERIAL PRIMARY KEY,
    from_entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    to_entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    rel_type TEXT NOT NULL, -- synonym | antonym | ngoko_to_krama | krama_to_ngoko | variant | root | derived
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (from_entry_id, to_entry_id, rel_type)
);

-- 6. Crawl pages: Javanese sites visited by scraper
CREATE TABLE IF NOT EXISTS crawl_pages (
    id SERIAL PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    domain TEXT,
    title TEXT,
    raw_text TEXT,
    lang_guess TEXT, -- jv | id | mixed | other
    javanese_score DOUBLE PRECISION DEFAULT 0,
    word_count INTEGER DEFAULT 0,
    unique_words INTEGER DEFAULT 0,
    known_words INTEGER DEFAULT 0,
    unknown_words INTEGER DEFAULT 0,
    fetched_at TIMESTAMPTZ DEFAULT now()
);

-- 7. Token observations: word frequencies seen in the wild
CREATE TABLE IF NOT EXISTS token_observations (
    page_id INTEGER NOT NULL REFERENCES crawl_pages(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    token_norm TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    is_known BOOLEAN DEFAULT FALSE,
    entry_id INTEGER REFERENCES entries(id) ON DELETE SET NULL,
    PRIMARY KEY (page_id, token_norm)
);
CREATE INDEX IF NOT EXISTS idx_tokens_norm ON token_observations (token_norm);

-- 8. Updated-at trigger
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_entries_touch ON entries;
CREATE TRIGGER trg_entries_touch BEFORE UPDATE ON entries
FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 9. Helpful views
CREATE OR REPLACE VIEW v_dictionary AS
SELECT e.id, e.headword, e.aksara_jawa, e.lang, e.pos, e.speech_level,
       s.name AS source_name,
       string_agg(d.definition, ' || ' ORDER BY d.id) AS definitions
FROM entries e
LEFT JOIN sources s ON s.id = e.source_id
LEFT JOIN definitions d ON d.entry_id = e.id
GROUP BY e.id, s.name;

CREATE OR REPLACE VIEW v_unknown_candidates AS
SELECT token_norm AS candidate, SUM(count) AS total_count,
       COUNT(DISTINCT page_id) AS pages,
       MAX(token) AS sample_form
FROM token_observations
WHERE is_known = FALSE
GROUP BY token_norm
ORDER BY total_count DESC;
