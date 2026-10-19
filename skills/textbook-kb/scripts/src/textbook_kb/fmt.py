"""Compact plain-text renderings of query results (token-efficient for agents)."""

from __future__ import annotations


def _pages(p) -> str:
    if not p or p[0] is None:
        return ""
    return f"p.{p[0]}" if p[0] == p[1] or p[1] is None else f"p.{p[0]}-{p[1]}"


def _head(d: dict) -> str:
    bits = [f"[c{d['chunk_id']}]", f"《{d['book_title']}》", d["path"]]
    tl = d["type"] + (f" {d['label']}" if d.get("label") else "")
    bits.append(f"| {tl}")
    if _pages(d.get("pages")):
        bits.append(f"| {_pages(d['pages'])}")
    bits.append(f"| L{d['lines'][0]}-{d['lines'][1]}")
    return " ".join(bits)


def books(items: list[dict]) -> str:
    if not items:
        return "（数据库中还没有书籍）"
    out = []
    for b in items:
        meta = " / ".join(x for x in (b["subject"], b["field"], b["level"], b["language"]) if x)
        auth = "、".join(b["authors"])
        out.append(f"- {b['id']}: 《{b['title']}》{' ' + auth if auth else ''}"
                   f"{' (' + meta + ')' if meta else ''} — {b['sections']} 节 / {b['chunks']} 块")
    return "\n".join(out)


def toc(d: dict) -> str:
    out = [f"《{d['title']}》 ({d['book']})"]
    base = min((s["level"] for s in d["sections"]), default=1)
    for s in d["sections"]:
        pad = "  " * (s["level"] - base)
        extra = f" [{s['role']}]" if s["role"] != "body" else ""
        pg = _pages(s["pages"])
        topics = f" #{' #'.join(s['topics'])}" if s["topics"] else ""
        out.append(f"{pad}- s{s['section_id']} {s['title']}{extra}{' ' + pg if pg else ''} "
                   f"({s['chars'] / 1000:.1f}k){topics}")
        if s.get("summary"):
            out.append(f"{pad}  > {s['summary']}")
    return "\n".join(out)


def search(d: dict) -> str:
    if not d["results"]:
        return f"未找到与「{d['query']}」匹配的内容（可尝试 --mode exact、换同义词或去掉过滤条件）"
    out = [f"{d['count']} 条结果 · 「{d['query']}」"]
    for r in d["results"]:
        flag = "" if r["match"] == "all-terms" else f" ({r['match']})"
        out.append(_head(r) + flag)
        out.append("    " + r["snippet"])
    out.append("→ 用 get <chunk_id> [--context 1] 读取全文")
    return "\n".join(out)


def _assets(a: list[dict]) -> str:
    paths = [x["path"] or f"(缺失) {x['ref']}" for x in a]
    return ("\n[图片] " + "\n[图片] ".join(paths)) if paths else ""


def chunk(d: dict) -> str:
    out = []
    for c in d["chunks"]:
        mark = ">>> " if c["is_target"] and len(d["chunks"]) > 1 else ""
        out.append(f"{mark}{_head(c)}")
        if c.get("refers_to"):
            out.append(f"(证明对象: c{c['refers_to']})")
        out.append(c["content"].rstrip() + _assets(c["assets"]))
        out.append("")
    nav = []
    if d.get("prev"):
        nav.append(f"prev=c{d['prev']}")
    if d.get("next"):
        nav.append(f"next=c{d['next']}")
    if nav:
        out.append("— " + "  ".join(nav))
    return "\n".join(out).rstrip()


def section(d: dict) -> str:
    head = f"《{d['book_title']}》 {d['path']} [s{d['section_id']}, {d['role']}]"
    if _pages(d.get("pages")):
        head += f" {_pages(d['pages'])}"
    out = [head]
    if d.get("summary"):
        out.append(f"> {d['summary']}")
    out += ["", d["content"].rstrip() + _assets(d["assets"])]
    if d["truncated"]:
        out.append(f"\n…（已截断；继续阅读：read {d['book']} s{d['section_id']} --from c{d['next_chunk']}）")
    return "\n".join(out)


def lookup(d: dict) -> str:
    out = []
    for m in d["matches"]:
        out.append(f"{m['kind']} {m['key']} → " + _head(m))
        if "content" in m:
            out.append(m["content"].rstrip() + _assets(m.get("assets", [])))
        elif m.get("latex"):
            out.append(f"$$ {m['latex']} $$")
        out.append("")
    for s in d["sections"]:
        pg = _pages(s["pages"])
        out.append(f"章节 → s{s['section_id']} 《{s['book_title']}》 {s['path']} [{s['role']}]"
                   f"{' ' + pg if pg else ''}  (read {s['book']} s{s['section_id']})")
    return "\n".join(out).rstrip() or f"未找到 {d['ref']}（可用 search 全文检索）"


def units(d: dict) -> str:
    if not d["units"]:
        return "（无匹配单元）"
    out = []
    for u in d["units"]:
        pg = _pages(u.get("pages"))
        out.append(f"- c{u['chunk_id']} {u['type']} {u['label']}{' ' + pg if pg else ''} | {u['path']}")
        out.append(f"    {u['preview']}")
    return "\n".join(out)
