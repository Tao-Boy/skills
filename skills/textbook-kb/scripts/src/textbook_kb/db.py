"""SQLite storage: books / sections / chunks / labels / assets + two FTS5 indexes.

- chunks_fts : CJK-bigram + porter-stemmed words, BM25 ranked (fuzzy keyword search)
- chunks_tri : trigram over corrected content (exact substring, incl. LaTeX like \\nabla)
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .build import BuildResult
from .textutil import index_text, sha256_bytes, slugify

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS books(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT, subject TEXT, field TEXT, level TEXT,
  language TEXT, edition TEXT, volume TEXT, publisher TEXT, year INTEGER, tags TEXT,
  description TEXT, quality TEXT, source_path TEXT, source_sha256 TEXT, source_bytes INTEGER,
  source_lines INTEGER, plan TEXT, imported_at TEXT);
CREATE TABLE IF NOT EXISTS sections(
  id INTEGER PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL, parent_id INTEGER, level INTEGER, number TEXT, title TEXT, path TEXT,
  role TEXT, chapter TEXT, topics TEXT, summary TEXT, start_line INTEGER, end_line INTEGER,
  page_start INTEGER, page_end INTEGER);
CREATE INDEX IF NOT EXISTS ix_sections_book ON sections(book_id, seq);
CREATE INDEX IF NOT EXISTS ix_sections_number ON sections(book_id, number);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE, seq INTEGER NOT NULL,
  type TEXT, label TEXT, title TEXT, start_line INTEGER, end_line INTEGER,
  page_start INTEGER, page_end INTEGER, content_raw TEXT NOT NULL, content TEXT NOT NULL,
  chars INTEGER, has_math INTEGER, ref_chunk_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_chunks_book ON chunks(book_id, seq);
CREATE INDEX IF NOT EXISTS ix_chunks_section ON chunks(section_id);
CREATE INDEX IF NOT EXISTS ix_chunks_type ON chunks(book_id, type);
CREATE TABLE IF NOT EXISTS labels(
  id INTEGER PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  kind TEXT, key TEXT, text TEXT, chapter TEXT, line INTEGER, extra TEXT);
CREATE INDEX IF NOT EXISTS ix_labels_key ON labels(key, kind);
CREATE TABLE IF NOT EXISTS assets(
  id INTEGER PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE, ref TEXT, path TEXT);
CREATE INDEX IF NOT EXISTS ix_assets_chunk ON assets(chunk_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  heading, body, tokenize='porter unicode61 remove_diacritics 2');
"""
TRIGRAM_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_tri USING fts5(
  content, content='chunks', content_rowid='id', tokenize='trigram');
"""


def default_db_path() -> Path:
    env = os.environ.get("TKB_DB")
    if env:
        return Path(env).expanduser()
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "textbook-kb" / "textbook.db"


def connect(path: str | os.PathLike | None = None, readonly: bool = False) -> sqlite3.Connection:
    p = Path(path).expanduser() if path else default_db_path()
    if readonly:
        if not p.exists():
            raise FileNotFoundError(f"数据库不存在: {p}（先用 `tkb ingest` 导入教材）")
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if not readonly:
        init(conn)
    return conn


def db_file(conn: sqlite3.Connection) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2])


def has_trigram(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE name='chunks_tri'").fetchone()
    return row is not None


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    try:
        conn.executescript(TRIGRAM_SCHEMA)
    except sqlite3.OperationalError:
        pass  # SQLite < 3.34: exact search falls back to LIKE
    conn.execute("INSERT OR IGNORE INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()


def remove_book(conn: sqlite3.Connection, book_id: str) -> bool:
    if not conn.execute("SELECT 1 FROM books WHERE id=?", (book_id,)).fetchone():
        return False
    ids = "SELECT id FROM chunks WHERE book_id=?"
    conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({ids})", (book_id,))
    if has_trigram(conn):
        conn.execute("INSERT INTO chunks_tri(chunks_tri, rowid, content) "
                     "SELECT 'delete', id, content FROM chunks WHERE book_id=?", (book_id,))
    conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    asset_dir = db_file(conn).parent / "assets" / slugify(book_id)
    if asset_dir.is_dir():
        shutil.rmtree(asset_dir)
    return True


def ingest(conn: sqlite3.Connection, plan: dict, res: BuildResult, source_bytes: bytes,
           replace: bool = False, copy_images: bool = True) -> dict:
    book = plan["book"]
    bid = book["id"]
    if conn.execute("SELECT 1 FROM books WHERE id=?", (bid,)).fetchone():
        if not replace:
            raise ValueError(f"书籍 {bid!r} 已存在；使用 --replace 覆盖")
    src = Path(plan["source"]["path"])
    try:
        conn.execute("BEGIN")
        remove_book(conn, bid)
        conn.execute(
            "INSERT INTO books VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, book.get("title") or bid, json.dumps(book.get("authors") or [], ensure_ascii=False),
             book.get("subject", ""), book.get("field", ""), book.get("level", ""),
             book.get("language", ""), book.get("edition", ""), str(book.get("volume", "") or ""),
             book.get("publisher", ""), book.get("year"),
             json.dumps(book.get("tags") or [], ensure_ascii=False), book.get("description", ""),
             book.get("quality", ""), str(src), sha256_bytes(source_bytes), len(source_bytes),
             plan["source"]["lines"], json.dumps(plan, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        sec_ids: dict[int, int] = {}
        for s in res.sections:
            cur = conn.execute(
                "INSERT INTO sections(book_id,seq,parent_id,level,number,title,path,role,chapter,topics,"
                "summary,start_line,end_line,page_start,page_end) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (bid, s.seq, sec_ids.get(s.parent) if s.parent is not None else None, s.level, s.number,
                 s.title, s.path, s.role, s.chapter, json.dumps(s.topics, ensure_ascii=False), s.summary,
                 s.start, s.end, s.page_start, s.page_end))
            sec_ids[s.seq] = cur.lastrowid
        chunk_ids: dict[int, int] = {}
        asset_root = db_file(conn).parent / "assets" / slugify(bid)
        n_assets = 0
        for c in res.chunks:
            sec = res.sections[c.section]
            cur = conn.execute(
                "INSERT INTO chunks(book_id,section_id,seq,type,label,title,start_line,end_line,page_start,"
                "page_end,content_raw,content,chars,has_math,ref_chunk_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (bid, sec_ids[c.section], c.seq, c.type, c.label, c.title, c.start, c.end, c.page_start,
                 c.page_end, c.raw, c.content, len(c.content), int(c.has_math),
                 chunk_ids.get(c.ref) if c.ref is not None else None))
            cid = cur.lastrowid
            chunk_ids[c.seq] = cid
            heading = " ".join(x for x in (sec.path, c.label, c.title) if x)
            conn.execute("INSERT INTO chunks_fts(rowid, heading, body) VALUES(?,?,?)",
                         (cid, index_text(heading), index_text(c.content)))
            for lb in c.labels:
                conn.execute("INSERT INTO labels(book_id,chunk_id,kind,key,text,chapter,line,extra) "
                             "VALUES(?,?,?,?,?,?,?,?)",
                             (bid, cid, lb.kind, lb.key, lb.text, sec.chapter, lb.line or c.start, lb.extra))
            for ref in dict.fromkeys(c.images):
                srcf = (src.parent / ref).resolve()
                dest = None
                if copy_images and srcf.is_file():
                    dest = asset_root / ref
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(srcf, dest)
                elif srcf.is_file():
                    dest = srcf
                conn.execute("INSERT INTO assets(book_id,chunk_id,ref,path) VALUES(?,?,?,?)",
                             (bid, cid, ref, str(dest) if dest else None))
                n_assets += 1
        if has_trigram(conn):
            conn.execute("INSERT INTO chunks_tri(rowid, content) SELECT id, content FROM chunks "
                         "WHERE book_id=?", (bid,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"book": bid, "sections": len(res.sections), "chunks": len(res.chunks),
            "labels": sum(len(c.labels) for c in res.chunks), "assets": n_assets}


def verify(conn: sqlite3.Connection, book_id: str | None = None) -> list[dict]:
    """Rebuild each book from its chunks and compare with the stored source hash."""
    rows = conn.execute("SELECT id, source_sha256, source_path FROM books" +
                        (" WHERE id=?" if book_id else ""), (book_id,) if book_id else ()).fetchall()
    out = []
    for r in rows:
        text = "".join(x[0] for x in conn.execute(
            "SELECT content_raw FROM chunks WHERE book_id=? ORDER BY seq", (r["id"],)))
        ok = sha256_bytes(text.encode("utf-8")) == r["source_sha256"]
        src = Path(r["source_path"])
        src_state = "missing"
        if src.is_file():
            src_state = "same" if sha256_bytes(src.read_bytes()) == r["source_sha256"] else "changed"
        out.append({"book": r["id"], "lossless": ok, "source_file": src_state})
    return out


def export_book(conn: sqlite3.Connection, book_id: str, corrected: bool = False) -> str:
    col = "content" if corrected else "content_raw"
    return "".join(x[0] for x in conn.execute(
        f"SELECT {col} FROM chunks WHERE book_id=? ORDER BY seq", (book_id,)))
