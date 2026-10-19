# 数据库结构（SQLite）

默认路径：`$BOOK_DB`，未设置时为 `~/.local/share/book-db/book.db`。图片会复制到数据库同级目录下的 `assets/<book_id>/`。

```sql
books(id PK, title, authors JSON, subject, field, level, language, edition, volume, publisher, year,
      tags JSON, description, quality, source_path, source_sha256, source_bytes, source_lines,
      plan JSON,            -- 导入时使用的完整 plan，可据此复现导入
      imported_at)

sections(id PK, book_id, seq, parent_id, level, number, title, path, role, chapter,
         topics JSON, summary, start_line, end_line, page_start, page_end)

chunks(id PK, book_id, section_id, seq,      -- seq：书内全局顺序；按 seq 拼接 content_raw 即得原文
       type, label, title, start_line, end_line, page_start, page_end,
       content_raw,                          -- MinerU 原文（逐字节）
       content,                              -- 修正层（去噪 + fixes）
       chars, has_math, ref_chunk_id)        -- ref_chunk_id：证明块所证明的定理块

labels(id PK, book_id, chunk_id, kind, key, text, chapter, line, extra)   -- extra：公式的 LaTeX
assets(id PK, book_id, chunk_id, ref, path)

chunks_fts(heading, body)   -- FTS5，porter+unicode61；预处理时中文切成二元组。heading = 章节路径 + 标签 + 标题
chunks_tri(content)         -- FTS5 trigram，外部内容表为 chunks.content，用于精确子串匹配（需要 SQLite ≥ 3.34）
```

页码是 PDF 页序号（从 1 开始），由 MinerU 的 `content_list.json` 对齐得到；没有该文件时为 NULL。

## SQL 示例

```sql
-- 每本书各类单元的数量
SELECT book_id, type, COUNT(*) FROM chunks GROUP BY book_id, type;

-- 某本书第 2 章（含子节）中的所有定理及其证明
SELECT t.label, t.content, p.content AS proof
FROM chunks t JOIN sections s ON s.id = t.section_id
LEFT JOIN chunks p ON p.ref_chunk_id = t.id
WHERE t.book_id = 'guo-electrodynamics-3e' AND s.chapter = '2' AND t.type = 'theorem'
ORDER BY t.seq;

-- 某个公式在各书中的出现位置（精确子串）
SELECT c.id, c.book_id, s.path FROM chunks_tri JOIN chunks c ON c.id = chunks_tri.rowid
JOIN sections s ON s.id = c.section_id WHERE chunks_tri MATCH '"\nabla \times"';

-- 复现一本书的原文
SELECT group_concat(content_raw, '') FROM (SELECT content_raw FROM chunks WHERE book_id = ? ORDER BY seq);
```

如果自行构造 FTS 查询，中文需要先按二元组切分（参见 `textutil.query_terms`），否则匹配不到结果。
