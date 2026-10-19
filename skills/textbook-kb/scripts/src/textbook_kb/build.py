"""Turn source + plan into sections, chunks and labels (pure, no I/O).

Invariant (checked): ''.join(chunk.raw for chunk in chunks) == source text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import heuristics as H
from .mdparse import Block, image_refs
from .textutil import norm_number

ROLE_DEFAULT_TYPE = {"exercises": "exercise", "answers": "solution", "toc": "toc", "index": "index",
                     "references": "reference", "front_matter": "front_matter", "summary": "summary"}
ENUM_RE = re.compile(r"^\s*(?:[（(]\s*(?:\d{1,2}|[ivx]{1,4}|[a-z])\s*[)）]|[①-⑳]|\d{1,2}\s*[)）])")
LONG_UNIT_TYPES = {"example", "exercise", "proof", "solution", "remark", "algorithm"}


@dataclass
class Section:
    seq: int
    level: int
    number: str
    title: str
    role: str
    topics: list
    summary: str
    start: int
    end: int
    parent: int | None = None
    path: str = ""
    chapter: str = ""
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class Label:
    kind: str
    key: str
    text: str
    line: int
    extra: str = ""


@dataclass
class Chunk:
    seq: int
    section: int
    type: str
    label: str = ""
    title: str = ""
    start: int = 0
    end: int = 0
    raw: str = ""
    content: str = ""
    page_start: int | None = None
    page_end: int | None = None
    has_math: bool = False
    ref: int | None = None
    labels: list[Label] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    closed: bool = False

    @property
    def chars(self) -> int:
        return sum(len(b.text) for b in self.blocks)

    @property
    def only_headings(self) -> bool:
        return all(b.kind == "heading" for b in self.blocks)


@dataclass
class BuildResult:
    sections: list[Section]
    chunks: list[Chunk]
    errors: list[str]
    warnings: list[str]


def _corrected_lines(lines: list[str], plan: dict, errors: list[str], warnings: list[str]) -> list[str]:
    out = list(lines)
    n = len(lines)
    for ln in plan.get("noise_lines", []):
        if not isinstance(ln, int) or not 1 <= ln <= n:
            errors.append(f"noise_lines: 行号越界 {ln}")
            continue
        out[ln - 1] = ""
    for k, fx in enumerate(plan.get("fixes", [])):
        a = fx.get("line")
        b = fx.get("end_line", a)
        find, repl = fx.get("find"), fx.get("replace", "")
        if not isinstance(a, int) or not isinstance(b, int) or not 1 <= a <= b <= n:
            errors.append(f"fixes[{k}]: 行号无效 {a}-{b}")
            continue
        if not find:
            errors.append(f"fixes[{k}]: 缺少 find")
            continue
        seg = "".join(lines[a - 1:b])
        cnt = seg.count(find)
        if cnt == 0:
            errors.append(f"fixes[{k}] L{a}: 在原文中找不到 find={find[:60]!r}")
            continue
        if cnt > 1 and not fx.get("all"):
            errors.append(f"fixes[{k}] L{a}: find 出现 {cnt} 次，请加长上下文或设置 \"all\": true")
            continue
        cur = "".join(out[a - 1:b])  # earlier fixes on the same lines are kept: apply in order
        if find not in cur:
            errors.append(f"fixes[{k}] L{a}: find 已被前面的 fix/noise_lines 改掉，请合并为一条")
            continue
        out[a - 1] = cur.replace(find, repl) if fx.get("all") else cur.replace(find, repl, 1)
        for i in range(a, b):
            out[i] = ""
    return out


def _safe_boundary(prev: Block | None, b: Block) -> bool:
    if b.kind == "math" or prev is None:
        return False
    if prev.kind == "heading":
        return False
    first = b.text.lstrip()
    if b.kind == "paragraph" and (H.detect_caption(first) or H.CONTINUATION_RE.match(first)
                                  or H.TAG_LINE_RE.match(first)):
        return False
    if prev.kind == "paragraph":
        tail = prev.text.rstrip()[-1:]
        if tail in (":", "：") or H.detect_caption(prev.text):
            return False
    return True


def build(plan: dict, lines: list[str], blocks: list[Block]) -> BuildResult:
    errors: list[str] = []
    warnings: list[str] = []
    n = len(lines)
    raw_secs = plan.get("sections") or []
    starts_ok = {b.start for b in blocks}
    by_start = {b.start: b for b in blocks}

    # ---- sections
    if not raw_secs:
        errors.append("plan.sections 为空")
        return BuildResult([], [], errors, warnings)
    if raw_secs[0].get("line") != 1:
        errors.append("第一个 section 必须从第 1 行开始（可用 role=front_matter 的占位章节）")
    prev_line, prev_level = 0, 0
    sections: list[Section] = []
    for i, s in enumerate(raw_secs):
        ln, lv = s.get("line"), s.get("level")
        if not isinstance(ln, int) or not 1 <= ln <= max(n, 1):
            errors.append(f"sections[{i}]: line 无效 {ln}")
            continue
        if ln <= prev_line:
            errors.append(f"sections[{i}] L{ln}: 行号必须严格递增（上一个 L{prev_line}）")
            continue
        if ln != 1 and ln not in starts_ok:
            errors.append(f"sections[{i}] L{ln}: 不是块起始行（空行或块内部）")
            continue
        if not isinstance(lv, int) or not 1 <= lv <= 6:
            errors.append(f"sections[{i}] L{ln}: level 必须是 1-6，得到 {lv}")
            continue
        if lv > prev_level + 1 and prev_level:
            warnings.append(f"sections[{i}] L{ln}: 层级从 {prev_level} 跳到 {lv}")
        role = s.get("role") or "body"
        if role not in H.ROLES:
            errors.append(f"sections[{i}] L{ln}: 未知 role {role!r}，可选 {H.ROLES}")
            continue
        sections.append(Section(len(sections), lv, str(s.get("number") or ""), str(s.get("title") or ""),
                                role, list(s.get("topics") or []), str(s.get("summary") or ""), ln, n))
        prev_line, prev_level = ln, lv
    for a, b in zip(sections, sections[1:]):
        a.end = b.start - 1
    stack: list[Section] = []
    for s in sections:
        while stack and stack[-1].level >= s.level:
            stack.pop()
        s.parent = stack[-1].seq if stack else None
        s.path = " > ".join([x.title for x in stack] + [s.title])
        chap = next((x.number for x in stack + [s] if x.number), "")
        s.chapter = chap.split(".")[0] if chap else ""
        stack.append(s)

    corrected = _corrected_lines(lines, plan, errors, warnings)

    overrides: dict[int, dict] = {}
    for k, u in enumerate(plan.get("units", [])):
        ln = u.get("line")
        if ln not in by_start:
            errors.append(f"units[{k}]: L{ln} 不是块起始行")
            continue
        if u.get("type") not in (None, *H.CHUNK_TYPES):
            errors.append(f"units[{k}] L{ln}: 未知 type {u.get('type')!r}")
            continue
        overrides[ln] = u
    if errors:
        return BuildResult(sections, [], errors, warnings)

    ck = plan.get("chunking", {})
    target = int(ck.get("target_chars", 1200))
    max_chars = int(ck.get("max_chars", 3000))
    stmt_chars = int(ck.get("statement_chars", 800))

    # ---- chunks
    noise = {ln for ln in plan.get("noise_lines", []) if isinstance(ln, int)}
    chunks: list[Chunk] = []
    bi = 0
    for sec in sections:
        sec_blocks = []
        while bi < len(blocks) and blocks[bi].start <= sec.end:
            if blocks[bi].start >= sec.start:
                sec_blocks.append(blocks[bi])
            bi += 1
        default_type = ROLE_DEFAULT_TYPE.get(sec.role, "text")
        cur: Chunk | None = None
        last_statement: int | None = None
        sec_chunks: list[Chunk] = []

        def open_chunk(typ: str, label: str = "", title: str = "", number: str = "") -> Chunk:
            c = Chunk(len(chunks) + len(sec_chunks), sec.seq, typ, label, title)
            if number and typ in H.UNIT_TYPES:
                c.labels.append(Label(typ, number, label, 0))
            sec_chunks.append(c)
            return c

        for b in sec_blocks:
            if cur is not None and b.start in noise and b.start == b.end:
                cur.blocks.append(b)  # page numbers / running heads never start or split a chunk
                continue
            hit: H.UnitHit | None = None
            ov = overrides.get(b.start)
            if ov is not None:
                if ov.get("type"):
                    num = norm_number(str(ov.get("number") or "")) if ov.get("number") else ""
                    hit = H.UnitHit(ov["type"], num, ov.get("title", ""), ov.get("label") or ov["type"])
            elif b.kind in ("paragraph", "heading") and b.start != sec.start:
                hit = H.detect_unit(b.heading if b.kind == "heading" else b.text)
                if hit is None and sec.role == "exercises" and b.kind == "paragraph":
                    item = H.detect_exercise_item(b.text)
                    if item:
                        key = item if "." in item or not sec.chapter else f"{sec.chapter}.{item}"
                        hit = H.UnitHit("exercise", key, "", f"{sec.title} 第{item}题")
            prev = next((x for x in reversed(cur.blocks) if x.start not in noise), None) if cur else None
            start_new, typ, label, title, number = False, default_type, "", "", ""
            if cur is None:
                start_new = True
                if hit and b.start != sec.start:
                    typ, label, title, number = hit.type, hit.label, hit.title, hit.number
            elif hit is not None:
                if hit.type == "solution" and cur.type in ("example", "exercise") and ov is None:
                    pass
                elif cur.only_headings:
                    cur.type, cur.label, cur.title = hit.type, hit.label, hit.title
                    if hit.number and hit.type in H.UNIT_TYPES:
                        cur.labels.append(Label(hit.type, hit.number, hit.label, b.start))
                    if hit.type in H.STATEMENT_TYPES:
                        last_statement = cur.seq
                else:
                    start_new, typ, label, title, number = True, hit.type, hit.label, hit.title, hit.number
            elif cur.closed and b.kind != "math":
                start_new = True
            else:
                soft = max_chars if cur.type in LONG_UNIT_TYPES else target
                if cur.type in H.STATEMENT_TYPES:
                    # a statement keeps its formulas, "其中…" lines and enumerated conditions, then ends
                    enum = bool(ENUM_RE.match(b.text)) and cur.chars < stmt_chars
                    soft = 10**9 if enum or not any(x.kind == "paragraph" for x in cur.blocks) else 0
                if cur.chars >= soft and _safe_boundary(prev, b):
                    start_new = True
                    if cur.type in LONG_UNIT_TYPES:
                        typ, label, title = cur.type, cur.label, (cur.title or cur.label) + "（续）"
                elif cur.chars >= 2 * max_chars and b.kind != "math" and prev is not None:
                    start_new = True
                    warnings.append(f"L{b.start}: 无安全切分点，强制切分（chunk 已 {cur.chars} 字符）")
                    if cur.type != "text":
                        typ, label, title = cur.type, cur.label, (cur.title or cur.label) + "（续）"
            if start_new:
                cur = open_chunk(typ, label, title, number)
                if number and cur.labels:
                    cur.labels[-1].line = b.start
                if typ == "proof":
                    cur.ref = last_statement
                if typ in H.STATEMENT_TYPES:
                    last_statement = cur.seq
            cur.blocks.append(b)
            if cur.type == "proof" and H.END_MARK_RE.search(b.text):
                cur.closed = True
            # labels carried by this block
            if b.kind == "math":
                cur.has_math = True
                for t in H.equation_tags(b.text):
                    cur.labels.append(Label("equation", t, f"({t})", b.start, H.math_body(b.text)))
            elif b.kind == "paragraph":
                m = H.TAG_LINE_RE.match(b.text.strip())
                if m and prev is not None and prev.kind == "math" and not H.equation_tags(prev.text):
                    t = norm_number(m.group(1))
                    cur.labels.append(Label("equation", t, f"({t})", prev.start, H.math_body(prev.text)))
                cap = H.detect_caption(b.text)
                if cap:
                    cur.labels.append(Label(cap[0], cap[1], cap[2], b.start))
                if "$" in b.text:
                    cur.has_math = True
            elif b.kind == "table":
                cap = H.detect_caption(b.text)
                if cap:
                    cur.labels.append(Label(cap[0], cap[1], cap[2], b.start))
            cur.images.extend(image_refs(b.text))

        if not sec_chunks:  # empty section (only blank lines)
            sec_chunks.append(Chunk(len(chunks), sec.seq, default_type))
        # line spans: contiguous, cover the whole section
        for i, c in enumerate(sec_chunks):
            c.start = sec.start if i == 0 else c.blocks[0].start
        for i, c in enumerate(sec_chunks):
            c.end = sec_chunks[i + 1].start - 1 if i + 1 < len(sec_chunks) else sec.end
        chunks.extend(sec_chunks)

    for c in chunks:
        c.raw = "".join(lines[c.start - 1:c.end])
        c.content = "".join(corrected[c.start - 1:c.end])
        pages = [p for b in c.blocks for p in (b.page, b.page_end) if p is not None]
        if pages:
            c.page_start, c.page_end = min(pages), max(pages)
        if not c.label and c.type == "text" and c.blocks and c.blocks[0].kind == "heading":
            c.title = sections[c.section].title
    for s in sections:
        pages = [p for c in chunks if c.section == s.seq for p in (c.page_start, c.page_end) if p]
        if pages:
            s.page_start, s.page_end = min(pages), max(pages)

    for fx in plan.get("fixes", []):
        a, b2 = fx["line"], fx.get("end_line", fx["line"])
        owners = {c.seq for c in chunks if c.start <= a <= c.end or c.start <= b2 <= c.end}
        if len(owners) > 1:
            warnings.append(f"fix L{a}-{b2} 跨越 chunk 边界，修正文本将全部放在第一个 chunk")

    if "".join(c.raw for c in chunks) != "".join(lines):
        errors.append("内部错误：chunk 拼接结果与原文不一致（无损校验失败）")
    for s in sections:
        size = sum(len(c.content) for c in chunks if c.section == s.seq)
        if size > 30000:
            warnings.append(f"章节 L{s.start} 「{s.title}」有 {size} 字符，可能漏识别了下级标题")
    return BuildResult(sections, chunks, errors, warnings)


def stats(res: BuildResult) -> dict:
    from collections import Counter
    sizes = sorted(len(c.content) for c in res.chunks) or [0]
    return {
        "sections": len(res.sections),
        "chunks": len(res.chunks),
        "chunk_types": dict(Counter(c.type for c in res.chunks).most_common()),
        "labels": dict(Counter(lb.kind for c in res.chunks for lb in c.labels).most_common()),
        "chunk_chars": {"min": sizes[0], "median": sizes[len(sizes) // 2], "max": sizes[-1]},
        "roles": dict(Counter(s.role for s in res.sections).most_common()),
        "lossless": not any("无损" in e for e in res.errors),
    }


def preview(res: BuildResult, section_ref: str | None = None, line: int | None = None,
            limit: int = 80) -> str:
    secs = res.sections
    if line is not None:
        secs = [s for s in secs if s.start <= line <= s.end]
    elif section_ref:
        secs = [s for s in secs if s.number == section_ref or section_ref in s.title]
    out = []
    for s in secs[:5]:
        out.append(f"## [{s.role}] {s.path}  (L{s.start}-{s.end})")
        for c in [c for c in res.chunks if c.section == s.seq][:limit]:
            first = re.sub(r"\s+", " ", c.content.strip())[:70]
            labels = ", ".join(f"{lb.kind}:{lb.key}" for lb in c.labels)
            out.append(f"  - #{c.seq} {c.type}{' ' + c.label if c.label else ''} L{c.start}-{c.end} "
                       f"{len(c.content)}ch{' {' + labels + '}' if labels else ''} | {first}")
    return "\n".join(out) or "（无匹配章节）"
