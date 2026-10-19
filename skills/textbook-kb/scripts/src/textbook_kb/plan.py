"""Import plans: heuristic draft, (de)serialisation, outline rendering.

A plan never contains text to be inserted into the book. It only references
source line numbers (structure, labels, noise lines) plus targeted `fixes`
that are applied to the *corrected* layer while the raw text is kept verbatim.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from . import heuristics as H
from .mdparse import Block
from .textutil import nfkc, slugify

PLAN_VERSION = 1
BODY_START = ("绪论", "引言", "导论", "导言", "概论", "introduction", "prologue")
SECTION_KEYS = ("line", "level", "number", "title", "role", "topics", "summary")
DEFAULT_CHUNKING = {"target_chars": 1200, "max_chars": 3000, "statement_chars": 800}


def _block_chars(blocks: list[Block]) -> list[int]:
    return [len(b.text) for b in blocks]


def _promotable(b: Block) -> bool:
    """Short single-line paragraphs that look like headings MinerU missed."""
    t = b.text.strip()
    if b.kind != "paragraph" or "\n" in t or len(t) > 40 or len(t) < 2:
        return False
    if t[-1] in "。，,；;：:！？!?" or "$" in t or H.detect_unit(t) or H.detect_caption(t):
        return False
    t = nfkc(H.clean_heading(t))
    if re.match(rf"^第\s*[0-9{H.CN_NUM_CLASS}]+\s*[章篇]\s*\S", t):
        return True
    return bool(re.match(r"^\d+\.\d+(?:\.\d+)?\s+[一-鿿A-Za-z]", t)) and not H.DOT_LEADER_RE.search(t)


def draft_plan(source: Path, sha: str, n_lines: int, blocks: list[Block],
               content_list: Path | None, noise: list[int]) -> dict:
    cands: list[tuple[Block, H.HeadingInfo, bool]] = []
    for b in blocks:
        if b.kind == "heading":
            cands.append((b, H.classify_heading(b.heading), False))
        elif _promotable(b):
            cands.append((b, H.classify_heading(b.text.strip()), True))

    # characters of body text owned by each candidate (until the next candidate)
    cand_lines = [b.start for b, _, _ in cands]
    body_chars, prose_chars = [], []
    bi = 0
    for k, (b, _, _) in enumerate(cands):
        nxt = cand_lines[k + 1] if k + 1 < len(cands) else 10**12
        total = prose = 0
        while bi < len(blocks) and blocks[bi].start < nxt:
            blk = blocks[bi]
            if blk.start > b.start:
                total += len(blk.text)
                if not (H.is_page_number(blk.text) or H.DOT_LEADER_RE.search(blk.text.strip())):
                    prose += len(blk.text)
            bi += 1
        body_chars.append(total)
        prose_chars.append(prose)

    title_counts = Counter(re.sub(r"\s+", "", i.title) for _, i, _ in cands)
    kept: list[tuple[Block, H.HeadingInfo, bool, int]] = []
    demoted: list[dict] = []
    in_toc, toc_seen = False, set()

    def demote(b: Block, reason: str) -> None:
        demoted.append({"line": b.start, "text": b.text.strip()[:80], "reason": reason})

    for k, (b, info, promoted) in enumerate(cands):
        key = re.sub(r"\s+", "", H.DOT_LEADER_RE.sub("", info.title))
        if info.role == "toc":
            if info.kind == "toc_line":
                demote(b, "目录条目")
                continue
            in_toc = True
            kept.append((b, info, promoted, body_chars[k]))
            continue
        if in_toc:
            # TOC entries own (almost) no body text; a heading followed by real prose ends the TOC
            if key in toc_seen or prose_chars[k] > 20:
                in_toc = False
            else:
                toc_seen.add(key)
                demote(b, "位于目录中")
                continue
        if not promoted and (H.detect_unit(info.title) or H.detect_caption(info.title)):
            demote(b, "定理/例题/图注等标签，按单元处理")
            continue
        if info.kind == "plain" and len(info.title) > 60:
            demote(b, "过长，疑似正文")
            continue
        if info.kind == "plain" and title_counts[re.sub(r"\s+", "", info.title)] >= 3:
            demote(b, "重复标题，疑似页眉")
            continue
        if H.is_page_number(info.title):
            demote(b, "页码")
            continue
        kept.append((b, info, promoted, body_chars[k]))

    has_part = any(i.kind == "part" for _, i, _, _ in kept)
    offset = 1 if has_part else 0
    md_levels = {b.level for b, _, p, _ in kept if not p}
    flat_md = len(md_levels) <= 1
    sections: list[dict] = []
    last_struct_level, chapter_level, md_delta, enum_level = 0, None, 0, None
    seen_body = False
    role_stack: list[tuple[int, str]] = []
    book_title = ""

    for b, info, promoted, chars in kept:
        conf = info.conf
        if info.kind == "part":
            level = 1
        elif info.depth is not None:
            level = info.depth + offset
        elif info.role in ("exercises", "summary", "answers"):
            level = (chapter_level + 1) if chapter_level else 1
        elif info.kind == "cn_enum":
            level = last_struct_level + 1
            enum_level = level
        elif info.kind == "cn_paren":
            level = (enum_level or last_struct_level + 1) + 1
        elif not flat_md and not promoted:
            level = b.level + md_delta
            conf = "low"
        else:
            level = last_struct_level + 1 if last_struct_level else 1
            conf = "low"
        level = max(1, min(level, 6))
        if info.kind in ("part", "chapter", "section", "numbered", "special") and info.depth is not None:
            last_struct_level = level
            if not promoted:
                md_delta = level - b.level
            enum_level = None
            if info.kind == "chapter" or (info.kind == "special" and info.role == "appendix"):
                chapter_level = level
            elif info.kind == "numbered" and "." not in info.number:
                chapter_level = level
        if info.kind in ("part", "chapter", "section", "numbered") or \
                re.sub(r"\s+", "", info.title).lower().startswith(BODY_START):
            seen_body = True
        while role_stack and role_stack[-1][0] >= level:
            role_stack.pop()
        parent_role = role_stack[-1][1] if role_stack else None
        if info.role:
            role = info.role
            if role in ("index", "references", "appendix", "answers"):
                seen_body = True
        elif parent_role in ("appendix", "answers", "exercises", "front_matter", "preface",
                             "references", "index", "back_matter", "toc"):
            role = parent_role
        else:
            role = "body" if seen_body else "front_matter"
        role_stack.append((level, role))
        if not book_title and role == "front_matter" and info.kind == "plain" and len(info.title) <= 40:
            book_title = info.title
        sec = {"line": b.start, "level": level, "number": info.number, "title": info.title,
               "role": role, "topics": [], "summary": "", "_chars": chars, "_conf": conf}
        if promoted:
            sec["_promoted"] = True
        sections.append(sec)

    if not sections or sections[0]["line"] != 1:
        first_chars = sum(len(b.text) for b in blocks if not sections or b.start < sections[0]["line"])
        sections.insert(0, {"line": 1, "level": 1, "number": "", "title": "[前置内容]",
                            "role": "front_matter", "topics": [], "summary": "",
                            "_chars": first_chars, "_conf": "auto"})

    return {
        "version": PLAN_VERSION,
        "source": {"path": str(source), "sha256": sha, "lines": n_lines,
                   "content_list": str(content_list) if content_list else None},
        "book": {"id": slugify(source.stem), "title": book_title or source.stem, "authors": [],
                 "subject": "", "field": "", "level": "", "language": _guess_lang(blocks),
                 "edition": "", "volume": "", "publisher": "", "year": None, "tags": [],
                 "description": "", "quality": ""},
        "chunking": dict(DEFAULT_CHUNKING),
        "sections": sections,
        "units": [],
        "fixes": [],
        "noise_lines": noise,
        "demoted": demoted,
        "notes": "",
        "status": "draft",
    }


def _guess_lang(blocks: list[Block]) -> str:
    sample = "".join(b.text for b in blocks[:400] if b.kind == "paragraph")[:20000]
    cjk = len(re.findall(r"[一-鿿]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    return "zh" if cjk >= latin * 0.3 else "en"


def suggest_noise(blocks: list[Block]) -> list[int]:
    counts = Counter(b.text.strip() for b in blocks
                     if b.kind == "paragraph" and "\n" not in b.text.strip() and len(b.text.strip()) <= 40)
    out = []
    for b in blocks:
        if b.kind != "paragraph":
            continue
        t = b.text.strip()
        if H.is_page_number(t) or (counts.get(t, 0) >= 5 and not H.detect_unit(t)
                                   and not H.detect_caption(t) and "$" not in t):
            out.append(b.start)
    return out


# ------------------------------------------------------------------ I/O

def dump_plan(plan: dict) -> str:
    """JSON with one list item per line so the agent can edit sections with small diffs."""
    parts = []
    for k, v in plan.items():
        key = json.dumps(k)
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            items = ",\n".join("    " + json.dumps(x, ensure_ascii=False) for x in v)
            parts.append(f"  {key}: [\n{items}\n  ]")
        elif isinstance(v, list) and v and all(isinstance(x, int) for x in v):
            rows = [", ".join(str(x) for x in v[i:i + 20]) for i in range(0, len(v), 20)]
            parts.append(f"  {key}: [\n    " + ",\n    ".join(rows) + "\n  ]")
        elif isinstance(v, dict):
            body = json.dumps(v, ensure_ascii=False, indent=2).replace("\n", "\n  ")
            parts.append(f"  {key}: {body}")
        else:
            parts.append(f"  {key}: {json.dumps(v, ensure_ascii=False)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def load_plan(path: str | Path) -> dict:
    p = Path(path)
    if p.is_dir():
        p = p / "plan.json"
    plan = json.loads(p.read_text(encoding="utf-8"))
    plan.setdefault("units", [])
    plan.setdefault("fixes", [])
    plan.setdefault("noise_lines", [])
    plan.setdefault("chunking", dict(DEFAULT_CHUNKING))
    for k, v in DEFAULT_CHUNKING.items():
        plan["chunking"].setdefault(k, v)
    return plan


def save_plan(plan: dict, path: str | Path) -> None:
    Path(path).write_text(dump_plan(plan), encoding="utf-8")


def outline_markdown(plan: dict, blocks: list[Block], issues_summary: str = "") -> str:
    secs = plan["sections"]
    starts = [s["line"] for s in secs]
    chars = [0] * len(secs)
    units = Counter()
    k = 0
    for b in blocks:
        while k + 1 < len(starts) and b.start >= starts[k + 1]:
            k += 1
        chars[k] += len(b.text)
        u = H.detect_unit(b.text) if b.kind in ("paragraph", "heading") else None
        if u:
            units[u.type] += 1
    kinds = Counter(b.kind for b in blocks)
    book = plan["book"]
    out = [f"# 大纲：{book.get('title', '')}  (`{book.get('id', '')}`)", "",
           f"- 源文件：`{plan['source']['path']}`（{plan['source']['lines']} 行，"
           f"{sum(len(b.text) for b in blocks)} 字符）",
           "- 块：" + "，".join(f"{k} {v}" for k, v in kinds.most_common()),
           "- 检测到的单元：" + ("，".join(f"{k} {v}" for k, v in units.most_common()) or "无"),
           f"- 章节：{len(secs)}；降级的伪标题：{len(plan.get('demoted', []))}；"
           f"噪声行：{len(plan.get('noise_lines', []))}"]
    if issues_summary:
        out.append(f"- 校验：{issues_summary}")
    out += ["", "图例：`?` 低置信度（需人工确认层级/角色），`↑` 从段落提升为标题，`!` 过大章节（可能漏标题）", "",
            "```text"]
    for s, c in zip(secs, chars):
        flags = ""
        if s.get("_conf") == "low":
            flags += "?"
        if s.get("_promoted"):
            flags += "↑"
        if c > 30000:
            flags += "!"
        indent = "  " * (int(s.get("level", 1)) - 1)
        out.append(f"L{s['line']:<7} {flags:<3}{indent}{s['title']}  [{s.get('role', 'body')}] "
                   f"{c / 1000:.1f}k")
    out.append("```")
    dem = plan.get("demoted", [])
    if dem:
        out += ["", "## 降级的伪标题（未作为章节）", ""]
        out += [f"- L{d['line']}: {d['text']} — {d['reason']}" for d in dem[:200]]
        if len(dem) > 200:
            out.append(f"- …共 {len(dem)} 条，见 plan.json 的 demoted")
    return "\n".join(out) + "\n"
