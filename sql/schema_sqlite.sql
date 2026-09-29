-- SQLite fallback schema (subset of schema.sql, no pg extensions)
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    url TEXT,
    kind TEXT NOT NULL DEFAULT 'dictionary',
    license TEXT,
    language_pair TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    notes TEXT
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headword TEXT NOT NULL,
    headword_norm TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'jv',
    pos TEXT,
    speech_level TEXT,
    aksara_jawa TEXT,
    pronunciation TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    source_ref TEXT,
    raw TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_entries_headword_lang_source ON entries(headword, lang);
CREATE INDEX IF NOT EXISTS idx_entries_norm ON entries(headword_norm);
CREATE TABLE IF NOT EXISTS definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    lang TEXT NOT NULL DEFAULT 'id',
    definition TEXT NOT NULL,
    context TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_definitions_entry ON definitions(entry_id);
CREATE TABLE IF NOT EXISTS examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
    javanese_text TEXT,
    translation_text TEXT,
    translation_lang TEXT DEFAULT 'id',
    source_url TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    to_entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    rel_type TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (from_entry_id, to_entry_id, rel_type)
);
CREATE TABLE IF NOT EXISTS crawl_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    domain TEXT,
    title TEXT,
    raw_text TEXT,
    lang_guess TEXT,
    javanese_score REAL DEFAULT 0,
    word_count INTEGER DEFAULT 0,
    unique_words INTEGER DEFAULT 0,
    known_words INTEGER DEFAULT 0,
    unknown_words INTEGER DEFAULT 0,
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS token_observations (
    page_id INTEGER NOT NULL REFERENCES crawl_pages(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    token_norm TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    is_known INTEGER DEFAULT 0,
    entry_id INTEGER REFERENCES entries(id) ON DELETE SET NULL,
    PRIMARY KEY (page_id, token_norm)
);
