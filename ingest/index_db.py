#!/usr/bin/env python3
"""Build the search index: SQLite + FTS5 over the whole corpus.

Step 1 of the search backend. Deliberately useful on its own — even if the API
never ships, this file answers corpus-wide questions that the static site
cannot, because ~28MB of 立法理由 can't be a browser-side index.

FTS5 tokenisation for Chinese: the built-in tokenizers are word-boundary based
and Chinese has no spaces, so `unicode61` would index each *run* of han
characters as one token and only exact-phrase queries would hit. The fix that
needs no extension is a **bigram** index — every adjacent character pair — which
is what makes substring search work. It costs roughly 2x the text size and is
the standard trick for CJK FTS without ICU.

Usage:
    python3 ingest/index_db.py            # build data/search.db
    python3 ingest/index_db.py "安全存量"  # build (if needed) + query
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DB = DATA / "search.db"


def bigrams(text: str) -> str:
    """CJK-safe tokens: bigrams for han runs, whole words for latin/digits.

    「安全存量」 -> 「安全 全存 存量」, so a query for 存量 matches. Latin words and
    numbers are left intact — splitting "SAP" into bigrams would be worse.
    """
    out = []
    for chunk in re.findall(r"[一-鿿]+|[A-Za-z0-9]+", text or ""):
        if "一" <= chunk[0] <= "鿿":
            out.extend(chunk[i:i + 2] for i in range(max(len(chunk) - 1, 1)))
        else:
            out.append(chunk.lower())
    return " ".join(out)


def build(conn: sqlite3.Connection) -> int:
    conn.executescript("""
        DROP TABLE IF EXISTS amendments;
        DROP TABLE IF EXISTS fts;
        CREATE TABLE amendments(
            id INTEGER PRIMARY KEY, law TEXT, slug TEXT, article TEXT, akey TEXT,
            term TEXT, session TEXT, who TEXT, status TEXT, billno TEXT,
            pdf TEXT, old TEXT, new TEXT, why TEXT);
        CREATE INDEX idx_slug ON amendments(slug);
        CREATE INDEX idx_status ON amendments(status);
        -- external-content FTS: tokens live here, the row text stays in
        -- `amendments`, so the index is not a second copy of the corpus.
        CREATE VIRTUAL TABLE fts USING fts5(
            body, content='', tokenize='unicode61 remove_diacritics 0');
    """)

    idx = json.loads((DATA / "index.json").read_text(encoding="utf-8"))
    rid = 0
    rows, ftsrows = [], []
    for entry in idx["laws"]:
        shard = DATA / f"{entry['slug']}.json"
        if not shard.exists():
            continue
        for r in json.loads(shard.read_text(encoding="utf-8")):
            rid += 1
            rows.append((rid, entry["law"], entry["slug"], r.get("article", ""),
                         r.get("key", ""), r.get("term", ""), r.get("session", ""),
                         r.get("who", ""), r.get("status", ""), r.get("billNo", ""),
                         r.get("pdf", ""), r.get("old", ""), r.get("new", ""),
                         r.get("why", "")))
            ftsrows.append((rid, bigrams(" ".join(
                (entry["law"], r.get("article", ""), r.get("who", ""),
                 r.get("why", ""), r.get("old", ""), r.get("new", ""))))))

    conn.executemany("INSERT INTO amendments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO fts(rowid, body) VALUES (?,?)", ftsrows)
    conn.commit()
    return rid


def search(conn: sqlite3.Connection, q: str, limit: int = 10) -> list:
    # Each whitespace-separated word becomes its own PHRASE (bigrams joined by
    # `+`), and the words are ANDed. Bigramming the whole query as one phrase
    # made any multi-word search impossible — "主管機關 衛生福利" required the two
    # to be adjacent in the text, which they never are.
    words = [w for w in q.split() if w.strip()]
    phrases = []
    for w in words:
        toks = bigrams(w).split()
        if toks:
            phrases.append("(" + " + ".join(f'"{t}"' for t in toks) + ")")
    if not phrases:
        return []
    expr = " AND ".join(phrases)
    return conn.execute("""
        SELECT a.law, a.article, a.who, a.status, substr(a.why,1,90)
        FROM fts JOIN amendments a ON a.id = fts.rowid
        WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?""", (expr, limit)).fetchall()


def main() -> int:
    DATA.mkdir(exist_ok=True)
    fresh = not DB.exists() or "--rebuild" in sys.argv
    conn = sqlite3.connect(DB)
    if fresh:
        n = build(conn)
        size = DB.stat().st_size / 1e6
        print(f"indexed {n} amendments -> {DB.name} ({size:.1f}MB)")
        if n < 10000:
            print(f"FATAL: only {n} rows indexed", file=sys.stderr)
            return 1

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for q in args:
        hits = search(conn, q)
        print(f"\n=== {q} — {len(hits)} hit(s)")
        for law, art, who, status, why in hits:
            print(f"  {law} {art} · {who[:18]} · {status}\n     {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
