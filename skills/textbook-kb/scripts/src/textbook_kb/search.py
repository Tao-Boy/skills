"""Query API shared by the CLI and the MCP server. All functions return plain dicts."""

from __future__ import annotations

import json
import re
import sqlite3

from .db import has_trigram
from .textutil import CN_NUM_CLASS, cn_to_int, make_snippet, nfkc, norm_number, query_terms

NOISY_ROLES = ("toc", "index")
DEMOTED_ROLES = "('front_matter', 'references', 'back_matter')"
UNIT_ALIASES = {
    "定义": "definition", "定理": "theorem", "引理": "lemma", "推论": "corollary", "命题": "proposition",
    "公理": "axiom", "公设": "axiom", "定律": "law", "例题": "example", "例": "example",
    "习题": "exercise", "练习": "exercise", "题": "exercise", "注": "remark", "算法": "algorithm",
    "图": "figure", "表": "table", "式": "equation", "公式": "equation", "方程": "equation",
    "definition": "definition", "def": "definition", "theorem": "theorem", "thm": "theorem",
    "lemma": "lemma", "corollary": "corollary", "cor": "corollary", "proposition": "proposition",
    "prop": "proposition", "axiom": "axiom", "law": "law", "example": "example", "ex": "example",
    "exercise": "exercise", "problem": "exercise", "remark": "remark", "algorithm": "algorithm",
    "figure": "figure", "fig": "figure", "table": "table", "equation": "equation", "eq": "equation",
}


class NotFound(LookupError):
    pass


# ------------------------------------------------------------------ helpers

def resolve_book(conn: sqlite3.Connection, book: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM books WHERE id=?", (book,)).fetchone()
    if row:
        return row
    rows = conn.execute("SELECT * FROM books WHERE title LIKE ? OR id LIKE ?",
                        (f"%{book}%", f"%{book}%")).fetchall()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise NotFound(f"找不到书籍 {book!r}；用 books 列出已导入书籍")
    raise NotFound(f"书籍 {book!r} 有歧义: " + ", ".join(r["id"] for r in rows))


def _book_ids(conn, books) -> list[str] | None:
    if not books:
        return None
    if isinstance(books, str):
        books = [books]
    return [resolve_book(conn, b)["id"] for b in books]


def _section_number_from_ref(ref: str) -> str | None:
    t = nfkc(ref).strip()
    m = re.match(rf"^第\s*([0-9{CN_NUM_CLASS}]+)\s*[章篇讲]$", t)
    if m:
        v = cn_to_int(m.group(1))
        return str(v) if v is not None else None
    m = re.match(r"^(?:chapter|ch\.?|section|sec\.?|§)\s*([\d.\-]+)$", t, re.I) or \
        re.match(r"^§?\s*(\d+(?:[.．\-]\d+)*)\s*(?:节|章)?$", t)
    if m:
        return norm_number(m.group(1))
    m = re.match(rf"^第\s*([0-9{CN_NUM_CLASS}]+)\s*节$", t)
    if m:
        return str(cn_to_int(m.group(1)))
    return None


def resolve_section(conn, book_id: str, ref: str | int) -> sqlite3.Row:
    if isinstance(ref, int) or re.fullmatch(r"s\d+", str(ref).strip()):
        sid = int(str(ref).strip().lstrip("s"))
        row = conn.execute("SELECT * FROM sections WHERE id=? AND book_id=?", (sid, book_id)).fetchone()
        if row:
            return row
    ref = str(ref).strip()
    num = _section_number_from_ref(ref)
    if num:
        rows = conn.execute("SELECT * FROM sections WHERE book_id=? AND number=? ORDER BY "
                            "CASE role WHEN 'body' THEN 0 ELSE 1 END, level, seq", (book_id, num)).fetchall()
        if rows:
            return rows[0]
    rows = conn.execute("SELECT * FROM sections WHERE book_id=? AND title=? ORDER BY seq",
                        (book_id, ref)).fetchall() or \
        conn.execute("SELECT * FROM sections WHERE book_id=? AND title LIKE ? ORDER BY level, seq",
                     (book_id, f"%{ref}%")).fetchall()
    if rows:
        return rows[0]
    raise NotFound(f"在 {book_id} 中找不到章节 {ref!r}；用 toc 查看目录")


def _chunk_meta(row: sqlite3.Row) -> dict:
    return {
        "chunk_id": row["id"], "book": row["book_id"], "book_title": row["book_title"],
        "section_id": row["section_id"], "path": row["path"], "type": row["type"],
        "label": row["label"] or "", "title": row["title"] or "",
        "pages": [row["page_start"], row["page_end"]] if row["page_start"] else None,
        "lines": [row["start_line"], row["end_line"]],
    }


CHUNK_SELECT = """SELECT c.*, s.path, s.role, b.title AS book_title FROM chunks c
JOIN sections s ON s.id=c.section_id JOIN books b ON b.id=c.book_id"""


def _filters(conn, books, types, section, subject, field, all_roles):
    where, args = [], []
    ids = _book_ids(conn, books)
    if ids:
        where.append(f"c.book_id IN ({','.join('?' * len(ids))})")
        args += ids
    if types:
        types = [types] if isinstance(types, str) else list(types)
        types = [UNIT_ALIASES.get(t.lower(), t) for t in types]
        where.append(f"c.type IN ({','.join('?' * len(types))})")
        args += types
    if section:
        if not ids or len(ids) != 1:
            raise NotFound("按章节过滤时必须指定唯一的 book")
        s = resolve_section(conn, ids[0], section)
        where.append("c.start_line >= ? AND c.start_line <= ?")
        args += [s["start_line"], s["end_line"]]
    if subject:
        where.append("b.subject = ?")
        args.append(subject)
    if field:
        where.append("b.field LIKE ?")
        args.append(f"%{field}%")
    if not all_roles:
        where.append(f"s.role NOT IN ({','.join('?' * len(NOISY_ROLES))})")
        args += list(NOISY_ROLES)
    return where, args


# ------------------------------------------------------------------ API

def list_books(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT b.*, (SELECT COUNT(*) FROM chunks WHERE book_id=b.id) AS n_chunks, "
                          "(SELECT COUNT(*) FROM sections WHERE book_id=b.id) AS n_sections FROM books b "
                          "ORDER BY subject, field, title"):
        out.append({"id": r["id"], "title": r["title"], "authors": json.loads(r["authors"] or "[]"),
                    "subject": r["subject"], "field": r["field"], "level": r["level"],
                    "language": r["language"], "edition": r["edition"], "volume": r["volume"],
                    "tags": json.loads(r["tags"] or "[]"), "description": r["description"],
                    "sections": r["n_sections"], "chunks": r["n_chunks"], "imported_at": r["imported_at"]})
    return out


def toc(conn, book: str, depth: int = 3, section: str | None = None) -> dict:
    b = resolve_book(conn, book)
    where, args = "book_id=? AND level<=?", [b["id"], depth]
    if section:
        s = resolve_section(conn, b["id"], section)
        where += " AND start_line>=? AND start_line<=?"
        args += [s["start_line"], s["end_line"]]
        args[1] = s["level"] + depth
    rows = conn.execute(
        f"SELECT s.*, (SELECT COUNT(*) FROM chunks c WHERE c.section_id=s.id) AS n_chunks, "
        f"(SELECT SUM(chars) FROM chunks c WHERE c.section_id=s.id) AS n_chars FROM sections s "
        f"WHERE {where} ORDER BY seq", args).fetchall()
    return {"book": b["id"], "title": b["title"], "sections": [
        {"section_id": r["id"], "level": r["level"], "number": r["number"], "title": r["title"],
         "role": r["role"], "pages": [r["page_start"], r["page_end"]] if r["page_start"] else None,
         "topics": json.loads(r["topics"] or "[]"), "summary": r["summary"], "chars": r["n_chars"] or 0}
        for r in rows]}


def search(conn, query: str, books=None, types=None, section=None, limit: int = 10,
           mode: str = "auto", subject=None, field=None, all_roles: bool = False) -> dict:
    query = query.strip()
    if not query:
        raise ValueError("query 不能为空")
    where, args = _filters(conn, books, types, section, subject, field, all_roles)
    wsql = (" AND " + " AND ".join(where)) if where else ""
    exact_q = query[1:-1] if len(query) > 2 and query[0] == query[-1] == '"' else None
    if mode == "auto" and (exact_q or "\\" in query or "^" in query or "_" in query
                           or len(query) == 1):
        mode = "exact"
    results: dict[int, dict] = {}

    def add(rows, how):
        for r in rows:
            if r["id"] not in results and len(results) < limit:
                d = _chunk_meta(r)
                d["match"] = how
                d["snippet"] = make_snippet(r["content"], exact_q or query)
                results[r["id"]] = d

    def run_exact(q):
        if len(q) >= 3 and has_trigram(conn):
            phrase = '"' + q.replace('"', '""') + '"'
            rows = conn.execute(f"{CHUNK_SELECT} JOIN chunks_tri t ON t.rowid=c.id WHERE chunks_tri MATCH ?"
                                f"{wsql} ORDER BY s.role IN {DEMOTED_ROLES}, bm25(chunks_tri) LIMIT ?",
                                [phrase, *args, limit]).fetchall()
        else:
            rows = conn.execute(f"{CHUNK_SELECT} WHERE instr(c.content, ?) > 0{wsql} "
                                f"ORDER BY s.role IN {DEMOTED_ROLES}, c.book_id, c.seq "
                                f"LIMIT ?", [q, *args, limit]).fetchall()
        add(rows, "exact")

    def run_bm25(terms, joiner, how):
        if not terms:
            return
        match = f" {joiner} ".join(terms)
        rows = conn.execute(f"{CHUNK_SELECT} JOIN chunks_fts f ON f.rowid=c.id WHERE chunks_fts MATCH ?"
                            f"{wsql} ORDER BY s.role IN {DEMOTED_ROLES}, bm25(chunks_fts, 2.0, 1.0) LIMIT ?",
                            [match, *args, limit * 2]).fetchall()
        add(rows, how)

    if mode == "exact":
        run_exact(exact_q or query)
    else:
        terms = query_terms(query)
        if mode in ("auto", "bm25"):
            run_bm25(terms, "AND", "all-terms")
            if mode == "auto" and len(results) < limit and len(query) <= 40:
                run_exact(query)
            if len(results) < limit and len(terms) > 1:
                run_bm25(terms, "OR", "partial")
        else:
            raise ValueError("mode 必须是 auto / bm25 / exact")
    return {"query": query, "mode": mode, "count": len(results), "results": list(results.values())}


def _assets(conn, chunk_id: int) -> list[dict]:
    return [{"ref": r["ref"], "path": r["path"]}
            for r in conn.execute("SELECT ref, path FROM assets WHERE chunk_id=?", (chunk_id,))]


def get_chunk(conn, chunk_id: int, context: int = 0, raw: bool = False) -> dict:
    row = conn.execute(f"{CHUNK_SELECT} WHERE c.id=?", (chunk_id,)).fetchone()
    if not row:
        raise NotFound(f"chunk {chunk_id} 不存在")
    rows = conn.execute(f"{CHUNK_SELECT} WHERE c.book_id=? AND c.seq BETWEEN ? AND ? ORDER BY c.seq",
                        (row["book_id"], row["seq"] - context, row["seq"] + context)).fetchall()
    col = "content_raw" if raw else "content"
    items = []
    for r in rows:
        d = _chunk_meta(r)
        d["content"] = r[col]
        d["is_target"] = r["id"] == chunk_id
        d["assets"] = _assets(conn, r["id"])
        d["labels"] = [dict(kind=x["kind"], key=x["key"]) for x in
                       conn.execute("SELECT kind, key FROM labels WHERE chunk_id=?", (r["id"],))]
        if r["ref_chunk_id"]:
            d["refers_to"] = r["ref_chunk_id"]
        items.append(d)
    nb = conn.execute("SELECT (SELECT id FROM chunks WHERE book_id=? AND seq=?-1),"
                      "(SELECT id FROM chunks WHERE book_id=? AND seq=?+1)",
                      (row["book_id"], rows[0]["seq"], row["book_id"], rows[-1]["seq"])).fetchone()
    return {"chunks": items, "prev": nb[0], "next": nb[1]}


def read_section(conn, book: str, section: str, max_chars: int = 20000, start_chunk: int | None = None,
                 raw: bool = False, include_subsections: bool = True) -> dict:
    b = resolve_book(conn, book)
    s = resolve_section(conn, b["id"], section)
    end = s["end_line"] if include_subsections else None
    if end is None:
        nxt = conn.execute("SELECT start_line FROM sections WHERE book_id=? AND seq=?",
                           (b["id"], s["seq"] + 1)).fetchone()
        end = nxt[0] - 1 if nxt else s["end_line"]
    rows = conn.execute(f"{CHUNK_SELECT} WHERE c.book_id=? AND c.start_line BETWEEN ? AND ? ORDER BY c.seq",
                        (b["id"], s["start_line"], end)).fetchall()
    if start_chunk:
        rows = [r for r in rows if r["id"] >= start_chunk]
    col = "content_raw" if raw else "content"
    parts, used, next_chunk = [], 0, None
    for r in rows:
        if used and used + len(r[col]) > max_chars:
            next_chunk = r["id"]
            break
        parts.append(r)
        used += len(r[col])
    assets = [a for r in parts for a in _assets(conn, r["id"])]
    return {"book": b["id"], "book_title": b["title"], "section_id": s["id"], "path": s["path"],
            "role": s["role"], "pages": [s["page_start"], s["page_end"]] if s["page_start"] else None,
            "lines": [s["start_line"], s["end_line"]], "summary": s["summary"],
            "topics": json.loads(s["topics"] or "[]"),
            "chunk_ids": [r["id"] for r in parts], "content": "".join(r[col] for r in parts),
            "truncated": next_chunk is not None, "next_chunk": next_chunk, "assets": assets}


REF_RE = re.compile(
    r"^\s*(?P<kw>[A-Za-z]+\.?|[一-鿿]{1,2})?\s*[（(]?\s*(?P<num>[A-Z]?\d+(?:\s*[.．\-－—–]\s*\d+)*[a-z']?)"
    r"\s*[)）]?\s*(?P<tail>[一-鿿]*)\s*$")


def parse_ref(ref: str) -> tuple[list[str], str] | None:
    """'定理2.3' -> (['theorem'], '2.3');  '(2.15)' -> (['equation'], '2.15');  '2.3' -> ([], '2.3')"""
    t = nfkc(ref).strip()
    t = re.sub(r"^第\s*", "", t)
    m = REF_RE.match(t)
    if not m:
        return None
    kw = (m.group("kw") or "").rstrip(".").lower()
    num = norm_number(m.group("num"))
    if kw:
        kind = UNIT_ALIASES.get(kw)
        if not kind:
            return None
        return [kind], num
    if t.startswith(("(", "（")):
        return ["equation"], num
    return [], num


def lookup(conn, ref: str, book: str | None = None, chapter: str | None = None, limit: int = 5,
           with_content: bool = True) -> dict:
    """Exact lookup of numbered objects: 定理2.3, 例1.2, 式(2.15), 图3-1, 习题4.5, 第2章, §2.3."""
    ids = _book_ids(conn, book)
    out: dict = {"ref": ref, "matches": [], "sections": []}
    sec_num = _section_number_from_ref(ref)
    section_style = bool(re.search(r"[章节篇讲§]|chapter|section|\bch\.|\bsec\.", ref, re.I))
    parsed = None if section_style else parse_ref(ref)
    if parsed is None and sec_num is None:
        raise ValueError(f"无法解析引用 {ref!r}；示例：定理2.3、例1.2、(2.15)、图3-1、习题4.5、第2章、§2.3")
    if parsed:
        kinds, key = parsed
        where, args = ["l.key = ?"], [key]
        if kinds:
            where.append(f"l.kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        if ids:
            where.append(f"l.book_id IN ({','.join('?' * len(ids))})")
            args += ids
        if chapter:
            where.append("l.chapter = ?")
            args.append(str(chapter))
        rows = conn.execute(f"SELECT l.kind, l.key, l.text, l.line, l.extra, l.chunk_id FROM labels l "
                            f"WHERE {' AND '.join(where)} ORDER BY l.book_id, l.line LIMIT ?",
                            [*args, limit * 3]).fetchall()
        seen = set()
        for r in rows:
            if r["chunk_id"] in seen or len(out["matches"]) >= limit:
                continue
            seen.add(r["chunk_id"])
            c = conn.execute(f"{CHUNK_SELECT} WHERE c.id=?", (r["chunk_id"],)).fetchone()
            d = {"kind": r["kind"], "key": r["key"], "text": r["text"], "line": r["line"], **_chunk_meta(c)}
            if r["kind"] == "equation":
                d["latex"] = r["extra"]
            if with_content:
                d["content"] = c["content"]
                d["assets"] = _assets(conn, c["id"])
            out["matches"].append(d)
    if sec_num and (not parsed or not parsed[0]):
        q = "SELECT s.*, b.title AS book_title FROM sections s JOIN books b ON b.id=s.book_id WHERE s.number=?"
        args = [sec_num]
        if ids:
            q += f" AND s.book_id IN ({','.join('?' * len(ids))})"
            args += ids
        for r in conn.execute(q + " ORDER BY s.book_id, s.seq LIMIT ?", [*args, limit]):
            out["sections"].append({"section_id": r["id"], "book": r["book_id"], "book_title": r["book_title"],
                                    "number": r["number"], "path": r["path"], "role": r["role"],
                                    "lines": [r["start_line"], r["end_line"]],
                                    "pages": [r["page_start"], r["page_end"]] if r["page_start"] else None})
    return out


def list_units(conn, book: str, types=None, section: str | None = None, limit: int = 200) -> dict:
    b = resolve_book(conn, book)
    where, args = ["c.book_id=?", "c.label != ''"], [b["id"]]
    if types:
        types = [types] if isinstance(types, str) else list(types)
        types = [UNIT_ALIASES.get(t.lower(), t) for t in types]
        where.append(f"c.type IN ({','.join('?' * len(types))})")
        args += types
    if section:
        s = resolve_section(conn, b["id"], section)
        where.append("c.start_line BETWEEN ? AND ?")
        args += [s["start_line"], s["end_line"]]
    rows = conn.execute(f"{CHUNK_SELECT} WHERE {' AND '.join(where)} AND c.title NOT LIKE '%（续）' "
                        f"ORDER BY c.seq LIMIT ?", [*args, limit]).fetchall()
    return {"book": b["id"], "units": [
        {**_chunk_meta(r), "preview": make_snippet(r["content"], r["label"] or "", 120)} for r in rows]}


def stats(conn) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {"books": q("SELECT COUNT(*) FROM books"), "sections": q("SELECT COUNT(*) FROM sections"),
            "chunks": q("SELECT COUNT(*) FROM chunks"), "labels": q("SELECT COUNT(*) FROM labels"),
            "chars": q("SELECT COALESCE(SUM(chars),0) FROM chunks"), "trigram": has_trigram(conn),
            "types": {r[0]: r[1] for r in conn.execute("SELECT type, COUNT(*) FROM chunks GROUP BY type")}}
