"""CLI: `python -m bausastra.cli <command>` (run from repo root with venv active)."""
from __future__ import annotations
import json
import logging
from pathlib import Path
import click
from sqlalchemy import text as stext

from .db import get_engine, init_postgres

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
from .scraper import scrape_url, analyze_text, load_dictionary_words
from . import bootstrap as bs

@click.group()
def cli():
    pass


@cli.command()
def initdb():
    """Create schema (Postgres if reachable, else SQLite fallback)."""
    eng = get_engine()
    if eng.dialect.name == "postgresql":
        init_postgres(eng)
        click.echo("Postgres schema initialized.")
    else:
        click.echo(f"SQLite fallback ready at data/bausastra.db")


@cli.command()
@click.option("--pairs", default="jav_en,ind_jav,jav_jav", help="HF configs comma-separated")
@click.option("--limit", default=2000, help="entries per pair (keep small for quick start)")
@click.option("--csv/--no-csv", default=True, help="also load sastra-jawa CSV")
@click.option("--csv-limit", default=20000)
@click.option("--dasanama/--no-dasanama", default=True, help="also load dasanama synonyms")
def bootstrap(pairs, limit, csv, csv_limit, dasanama):
    """Boost-start DB: seed + HuggingFace lexicon + sastra-jawa CSV + dasanama."""
    eng = get_engine()
    bs.load_seed(eng)
    if csv:
        try:
            bs.load_sastra_csv(eng, limit=csv_limit)
        except Exception as e:
            click.echo(f"CSV load failed: {e}")
    if dasanama:
        try:
            bs.load_dasanama(eng)
        except Exception as e:
            click.echo(f"Dasanama load failed: {e}")
    try:
        bs.load_huggingface(eng, pairs=tuple(pairs.split(",")), limit_per_pair=limit)
    except Exception as e:
        click.echo(f"HF load failed (gated dataset needs `hf auth login` + accept conditions): {e}")
    with eng.connect() as c:
        n = c.execute(stext("SELECT COUNT(*) FROM entries")).scalar()
    click.echo(f"Done. entries={n}")


@cli.command()
@click.option("--words", default="", help="comma-separated words for targeted KBJI lookup")
@click.option("--unknown-top", default=0, help="look up top-N unknown candidates from crawl data")
@click.option("--letters", default="", help="e.g. ABC: bulk-enumerate list pages for these letters")
@click.option("--pages", default=1, help="list pages per letter")
@click.option("--max-words", default=200, help="cap on lemma detail fetches for --letters")
def kbji(words, unknown_top, letters, pages, max_words):
    """Import from KBJI official site (no API key needed, polite HTML scrape)."""
    eng = get_engine()
    targets: list[str] = [w.strip() for w in words.split(",") if w.strip()]
    if unknown_top:
        with eng.connect() as c:
            try:
                rows = c.execute(stext(
                    "SELECT token_norm FROM token_observations WHERE is_known=0 "
                    "GROUP BY token_norm ORDER BY SUM(count) DESC LIMIT :lim"),
                    {"lim": unknown_top}).all()
            except Exception:
                rows = c.execute(stext(
                    "SELECT token_norm, SUM(count) FROM token_observations WHERE is_known=0 "
                    "GROUP BY token_norm ORDER BY 2 DESC LIMIT :lim"),
                    {"lim": unknown_top}).all()
                rows = [(r[0],) for r in rows]
        targets += [r[0] for r in rows if r[0] and len(r[0]) > 2]
    if targets:
        click.echo(f"KBJI targeted lookup: {len(targets)} words")
        bs.load_kbji_words(eng, targets)
    if letters:
        click.echo(f"KBJI bulk: letters={letters} pages={pages} max={max_words}")
        bs.load_kbji_letters(eng, letters=letters, pages_per_letter=pages, max_words=max_words)
    if not targets and not letters:
        raise click.UsageError("provide --words, --unknown-top N, or --letters ABC")
    with eng.connect() as c:
        n = c.execute(stext("SELECT COUNT(*) FROM entries")).scalar()
    click.echo(f"Done. entries={n}")


@cli.command()
@click.option("--url", required=False, help="single URL to scrape")
@click.option("--file", "file_", required=False, type=click.Path(exists=True), help="file with URLs, one per line")
@click.option("--limit", default=10, help="max URLs from file")
@click.option("--save/--no-save", default=True, help="persist crawl + tokens to DB")
def scrape(url, file_, limit, save):
    """Scrape Javanese site(s) and identify known vs unknown words."""
    eng = get_engine()
    dictionary = load_dictionary_words(eng)
    click.echo(f"Dictionary in DB: {len(dictionary)} headwords")
    urls: list[str] = []
    if url:
        urls.append(url)
    if file_:
        urls += [l.strip() for l in Path(file_).read_text(encoding="utf-8").splitlines()
                 if l.strip().startswith("http")][:limit]
    if not urls:
        raise click.UsageError("provide --url or --file")
    for u in urls:
        try:
            res = scrape_url(u, dictionary)
        except Exception as e:
            click.echo(f"FAIL {u}: {e}")
            continue
        click.echo(f"\n== {res['title']} ==\n{res['url']}")
        click.echo(f"lang={res['lang_guess']} jv_score={res['javanese_score']} "
                   f"words={res['word_count']} unique={res['unique_words']} "
                   f"known={res['known_words']} unknown={res['unknown_words']}")
        click.echo(f"top unknown: {list(res['unknown'].keys())[:15]}")
        if save:
            _save_crawl(eng, res, dictionary)


def _save_crawl(eng, res, dictionary):
    from .javanese import normalize as norm
    # all_counts is already capped in scraper.analyze_text; hard-cap again here.
    items = sorted(res["all_counts"].items(), key=lambda x: -x[1])[:5000]
    with eng.begin() as c:
        if eng.dialect.name == "postgresql":
            c.execute(stext("""
                INSERT INTO crawl_pages (url, domain, title, raw_text, lang_guess,
                    javanese_score, word_count, unique_words, known_words, unknown_words)
                VALUES (:url,:dom,:title,:txt,:lg,:sc,:wc,:uw,:kw,:unk)
                ON CONFLICT (url) DO UPDATE SET title=EXCLUDED.title, raw_text=EXCLUDED.raw_text,
                    lang_guess=EXCLUDED.lang_guess, javanese_score=EXCLUDED.javanese_score,
                    word_count=EXCLUDED.word_count, unique_words=EXCLUDED.unique_words,
                    known_words=EXCLUDED.known_words, unknown_words=EXCLUDED.unknown_words,
                    fetched_at=now()
            """), {"url": res["url"], "dom": res["domain"], "title": res["title"][:500],
                   "txt": res["raw_text"], "lg": res["lang_guess"], "sc": res["javanese_score"],
                   "wc": res["word_count"], "uw": res["unique_words"],
                   "kw": res["known_words"], "unk": res["unknown_words"]})
        else:
            c.execute(stext("""
                INSERT OR REPLACE INTO crawl_pages
                (url, domain, title, raw_text, lang_guess, javanese_score, word_count, unique_words, known_words, unknown_words)
                VALUES (:url,:dom,:title,:txt,:lg,:sc,:wc,:uw,:kw,:unk)
            """), {"url": res["url"], "dom": res["domain"], "title": res["title"][:500],
                   "txt": res["raw_text"], "lg": res["lang_guess"], "sc": res["javanese_score"],
                   "wc": res["word_count"], "uw": res["unique_words"],
                   "kw": res["known_words"], "unk": res["unknown_words"]})
        pid = c.execute(stext("SELECT id FROM crawl_pages WHERE url=:u"), {"u": res["url"]}).scalar()
        for tok, cnt in items:
            t = norm(tok)
            known = t in dictionary
            if eng.dialect.name == "postgresql":
                c.execute(stext("""
                    INSERT INTO token_observations (page_id, token, token_norm, count, is_known)
                    VALUES (:p,:t,:n,:c,:k)
                    ON CONFLICT (page_id, token_norm) DO UPDATE SET count=EXCLUDED.count, is_known=EXCLUDED.is_known
                """), {"p": pid, "t": tok[:200], "n": t[:200], "c": cnt, "k": known})
            else:
                c.execute(stext("""
                    INSERT OR REPLACE INTO token_observations (page_id, token, token_norm, count, is_known)
                    VALUES (:p,:t,:n,:c,:k)
                """), {"p": pid, "t": tok[:200], "n": t[:200], "c": int(cnt), "k": 1 if known else 0})


@cli.command()
@click.option("--text", required=True, help="Javanese/Indonesian text to analyze")
def identify(text):
    """Identify words in a pasted snippet against the dictionary."""
    eng = get_engine()
    dictionary = load_dictionary_words(eng)
    out = analyze_text(text, dictionary)
    click.echo(json.dumps({k: v for k, v in out.items() if k != "all_counts"},
                          ensure_ascii=False, indent=2))


@cli.command()
@click.option("--q", required=True, help="lookup headword (prefix search)")
@click.option("--limit", default=10)
def lookup(q, limit):
    """Look up words in local dictionary."""
    from .javanese import normalize as norm
    eng = get_engine()
    with eng.connect() as c:
        if eng.dialect.name == "postgresql":
            rows = c.execute(stext("""
                SELECT e.headword, e.lang, e.speech_level, s.name, d.definition
                FROM entries e LEFT JOIN sources s ON s.id=e.source_id
                LEFT JOIN definitions d ON d.entry_id=e.id
                WHERE e.headword_norm LIKE :q ORDER BY e.headword LIMIT :lim
            """), {"q": f"%{norm(q)}%", "lim": limit}).all()
        else:
            rows = c.execute(stext("""
                SELECT e.headword, e.lang, e.speech_level, s.name, d.definition
                FROM entries e LEFT JOIN sources s ON s.id=e.source_id
                LEFT JOIN definitions d ON d.entry_id=e.id
                WHERE e.headword_norm LIKE :q ORDER BY e.headword LIMIT :lim
            """), {"q": f"%{norm(q)}%", "lim": limit}).all()
    for hw, lang, lvl, src, df in rows:
        click.echo(f"{hw} [{lang}/{lvl}] ({src}): {df}")


@cli.command()
@click.option("--text", default="", help="Latin text to transliterate to Aksara Jawa")
@click.option("--reverse", default="", help="Aksara Jawa text to transliterate back to Latin")
@click.option("--backfill", is_flag=True, help="fill missing aksara_jawa for all jv/kawi entries")
def transliterate(text, reverse, backfill):
    """Transliterate via sinau tables, or backfill the DB."""
    from .transliterate import transliterate as tr, reverse_transliterate as rt, backfill_aksara
    if text:
        click.echo(tr(text))
    if reverse:
        click.echo(rt(reverse))
    if backfill:
        backfill_aksara(get_engine())
    if not text and not reverse and not backfill:
        raise click.UsageError("provide --text, --reverse, or --backfill")


@cli.command()
@click.option("--list", "list_", is_flag=True, help="show pending AI drafts")
@click.option("--limit", default=20, help="how many drafts to show")
@click.option("--approve", default=0, help="approve draft by def_id")
@click.option("--reject", default=0, help="reject draft by def_id")
@click.option("--text", default="", help="edited definition text for --approve")
def review(list_, limit, approve, reject, text):
    """Review AI drafts: --list, --approve ID [--text ...], --reject ID."""
    from . import review as rv
    if list_ or (not approve and not reject):
        q = rv.list_drafts(limit, 0)
        click.echo(f"Pending AI drafts: {q['total']}")
        for it in q["items"]:
            click.echo(f"  #{it['def_id']} {it['headword']} "
                       f"(conf {it['confidence']}): {it['definition'][:110]}")
    if approve:
        out = rv.approve(approve, text or None)
        click.echo(f"approve #{approve}: {out}")
    if reject:
        out = rv.reject(reject)
        click.echo(f"reject #{reject}: {out}")


@cli.command()
@click.option("--port", default=5000, help="port to listen on")
def serve(port):
    """Start the Bausastra JSON API (frontend in web/, served if built)."""
    from .api import app
    click.echo(f"Bausastra API at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)


@cli.group()
def apikey():
    """Manage public API keys (stored hashed, raw shown once)."""


@apikey.command("create")
@click.option("--name", required=True, help="app/user name for the key")
def apikey_create(name):
    from . import public as pub
    out = pub.create_key(get_engine(), name)
    click.echo(f"prefix: {out['prefix']}")
    click.echo(f"raw_key (STORE NOW, never shown again): {out['raw_key']}")


@apikey.command("list")
def apikey_list():
    from . import public as pub
    for k in pub.list_keys(get_engine()):
        click.echo(f"#{k['id']} {k['prefix']} {k['name']} revoked={k['revoked']} last={k['last_used_at']}")


@apikey.command("revoke")
@click.option("--id", "ident", required=True, help="key id or prefix")
def apikey_revoke(ident):
    from . import public as pub
    ok = pub.revoke_key(get_engine(), ident)
    click.echo(f"revoked={ok}")


if __name__ == "__main__":
    cli()
