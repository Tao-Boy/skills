"""Parse MinerU markdown into line-addressed blocks (never modifies the text).

Block kinds: heading, paragraph, math (display), table (HTML or pipe), image, code.
Every block records its 1-based inclusive line span, so any split made on block
boundaries is lossless by construction.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .textutil import strip_eol

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
IMAGE_RE = re.compile(r"^!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)$")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
IMG_REF_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?|<img[^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)

MAX_MATH_LINES = 400
MAX_TABLE_LINES = 2000


@dataclass
class Block:
    idx: int
    kind: str
    start: int
    end: int
    text: str
    level: int = 0
    heading: str = ""
    unclosed: bool = False
    page: int | None = None
    page_end: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def first_line(self) -> str:
        return self.text.split("\n", 1)[0]


def _line(lines: list[str], k: int) -> str:
    s = strip_eol(lines[k])
    return s.lstrip("﻿") if k == 0 else s


def _starts_block(st: str) -> bool:
    return bool(
        HEADING_RE.match(st) or st.startswith("$$") or st.startswith("\\[")
        or FENCE_RE.match(st) or "<table" in st.lower() or IMAGE_RE.match(st)
    )


def parse_blocks(lines: list[str]) -> list[Block]:
    n = len(lines)
    blocks: list[Block] = []
    i = 0

    def until_blank(j: int) -> int:
        while j + 1 < n and _line(lines, j + 1).strip():
            j += 1
        return j

    while i < n:
        st = _line(lines, i).strip()
        if not st:
            i += 1
            continue
        start, kind, level, heading, unclosed = i, "paragraph", 0, "", False
        m_fence = FENCE_RE.match(st)
        m_head = HEADING_RE.match(st)
        if m_fence:
            fence = m_fence.group(1)
            j = i + 1
            while j < n and not _line(lines, j).strip().startswith(fence):
                j += 1
            if j >= n:
                j, unclosed = until_blank(i), True
            end, kind = j, "code"
        elif st.startswith("$$") or st.startswith("\\["):
            opener, closer = ("$$", "$$") if st.startswith("$$") else ("\\[", "\\]")
            rest = st[len(opener):]
            kind = "math"
            if closer in rest:
                end = i
            else:
                j = i + 1
                while j < n and j - i <= MAX_MATH_LINES and closer not in _line(lines, j):
                    j += 1
                if j >= n or j - i > MAX_MATH_LINES:
                    j, unclosed = until_blank(i), True
                end = j
        elif "<table" in st.lower():
            kind = "table"
            j = i
            while j < n and j - i <= MAX_TABLE_LINES and "</table>" not in _line(lines, j).lower():
                j += 1
            if j >= n or j - i > MAX_TABLE_LINES:
                j, unclosed = until_blank(i), True
            end = j
        elif m_head:
            kind, level, heading, end = "heading", len(m_head.group(1)), m_head.group(2).strip(), i
        elif IMAGE_RE.match(st):
            kind, end = "image", i
        elif st.startswith("|"):
            kind = "table"
            j = i
            while j + 1 < n and _line(lines, j + 1).strip().startswith("|"):
                j += 1
            end = j
        else:
            j = i
            while j + 1 < n:
                nxt = _line(lines, j + 1).strip()
                if not nxt or _starts_block(nxt):
                    break
                j += 1
            end = j
        text = "\n".join(_line(lines, k) for k in range(start, end + 1))
        blocks.append(Block(len(blocks), kind, start + 1, end + 1, text, level, heading, unclosed))
        i = end + 1
    return blocks


def image_refs(text: str) -> list[str]:
    out = []
    for m in IMG_REF_RE.finditer(text):
        ref = m.group(1) or m.group(2)
        if ref and not ref.startswith(("http://", "https://", "data:")):
            out.append(ref)
    return out


# ---------------------------------------------------------------- page mapping

_NORM_RE = re.compile(r"[\s$#*`>|_\\{}]")


def _norm(s: str) -> str:
    return _NORM_RE.sub("", s or "")


def find_content_list(md_path: Path) -> Path | None:
    cands = [md_path.with_name(md_path.stem + "_content_list.json"),
             md_path.with_name("content_list.json")]
    cands += sorted(md_path.parent.glob("*_content_list.json"))
    for c in cands:
        if c.is_file():
            return c
    return None


def apply_pages(blocks: list[Block], content_list_path: str | os.PathLike | None) -> int:
    """Assign PDF page numbers (1-based) to blocks using MinerU's content_list.json.

    Greedy forward alignment by text probes; unmatched blocks inherit the previous
    page. Returns how many blocks were matched directly.
    """
    if not content_list_path:
        return 0
    try:
        items = json.loads(Path(content_list_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if isinstance(items, dict):
        items = items.get("content_list") or items.get("items") or []
    norms = [_norm(b.text) for b in blocks]
    ptr, matched = 0, 0
    for it in items:
        if not isinstance(it, dict) or it.get("page_idx") is None:
            continue
        typ = it.get("type", "text")
        if typ in ("image", "table") and it.get("img_path"):
            probe = os.path.basename(it["img_path"])
        elif typ == "table":
            probe = _norm(it.get("table_body", ""))[:30]
        else:
            probe = _norm(it.get("text", ""))[:30]
        if len(probe) < 2:
            continue
        page = int(it["page_idx"]) + 1
        for j in range(ptr, min(ptr + 120, len(blocks))):
            if probe in norms[j] or probe in blocks[j].text:
                b = blocks[j]
                if b.page is None:
                    b.page = page
                    matched += 1
                b.page_end = max(page, b.page_end or page)
                ptr = j
                break
    last = None
    for b in blocks:
        if b.page is None:
            b.page = last
        else:
            last = b.page_end or b.page
        if b.page_end is None:
            b.page_end = b.page
    return matched
