"""Deterministic checks on MinerU output. Findings are hints for the agent's
review; nothing here edits the source."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from . import heuristics as H
from .mdparse import Block, image_refs

INLINE_MATH_RE = re.compile(r"(?<!\\)\$(?!\$)(.+?)(?<!\\)\$")
TERMINAL = set("。！？.!?：:;；”\"’'）)]】$>」』…")


@dataclass
class Issue:
    code: str
    severity: str  # error | warn | info
    line: int
    msg: str
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _excerpt(s: str, n: int = 100) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n] + "…"


def _brace_delta(s: str) -> int:
    s = s.replace("\\{", "").replace("\\}", "")
    return s.count("{") - s.count("}")


def _check_math(body: str, line: int, out: list[Issue], raw: str) -> None:
    if _brace_delta(body):
        out.append(Issue("math.braces", "warn", line, "公式花括号不配对", _excerpt(raw)))
    begins = Counter(re.findall(r"\\begin\{([^}]+)\}", body))
    ends = Counter(re.findall(r"\\end\{([^}]+)\}", body))
    if begins != ends:
        out.append(Issue("math.env", "warn", line, f"\\begin/\\end 不匹配: {dict(begins)} vs {dict(ends)}",
                         _excerpt(raw)))
    if len(re.findall(r"\\left(?![a-zA-Z])", body)) != len(re.findall(r"\\right(?![a-zA-Z])", body)):
        out.append(Issue("math.leftright", "warn", line, "\\left/\\right 数量不一致", _excerpt(raw)))


def validate(lines: list[str], blocks: list[Block], md_path: str | Path | None = None) -> list[Issue]:
    out: list[Issue] = []
    base = Path(md_path).parent if md_path else None
    para_counter: Counter[str] = Counter()
    para_first_line: dict[str, int] = {}
    heading_titles: Counter[str] = Counter()
    heading_line: dict[str, int] = {}
    last_num: dict[str, int] = {}
    eq_tags: dict[str, int] = {}

    for i, b in enumerate(blocks):
        if b.unclosed:
            out.append(Issue(f"{b.kind}.unclosed", "error", b.start,
                             f"{b.kind} 块未闭合（解析到第 {b.end} 行为止）", _excerpt(b.text)))
        if b.kind == "math":
            body = H.math_body(b.text)
            if not body:
                out.append(Issue("math.empty", "warn", b.start, "空公式块", b.text))
            elif len(body) > 3000:
                out.append(Issue("math.long", "warn", b.start, f"公式块过长（{len(body)} 字符），可能多个公式被合并",
                                 _excerpt(body)))
            _check_math(body, b.start, out, b.text)
            for t in H.equation_tags(b.text):
                if t in eq_tags:
                    out.append(Issue("equation.duplicate_tag", "info", b.start,
                                     f"公式编号 ({t}) 重复，首次出现于 L{eq_tags[t]}", _excerpt(body, 60)))
                else:
                    eq_tags[t] = b.start
        elif b.kind == "paragraph":
            stripped = re.sub(r"\$\$.*?\$\$", "", b.text.replace("\\$", ""), flags=re.S)
            if stripped.count("$") % 2:
                out.append(Issue("math.inline_unbalanced", "warn", b.start, "行内公式 $ 数量为奇数",
                                 _excerpt(b.text)))
            for m in INLINE_MATH_RE.finditer(stripped):
                if _brace_delta(m.group(1)):
                    out.append(Issue("math.braces", "warn", b.start, "行内公式花括号不配对",
                                     _excerpt(m.group(0))))
                    break
            if "\ufffd" in b.text or len(re.findall(r"[\ue000-\uf8ff]", b.text)) > 2:
                out.append(Issue("text.garbled", "warn", b.start, "含替换字符/私有区字符，疑似乱码",
                                 _excerpt(b.text)))
            if len(b.text) > 5000:
                out.append(Issue("text.long_paragraph", "info", b.start,
                                 f"段落过长（{len(b.text)} 字符），可能缺少分段或双栏错序", _excerpt(b.text)))
            one = b.text.strip()
            if "\n" not in one and len(one) <= 40:
                para_counter[one] += 1
                para_first_line.setdefault(one, b.start)
            if H.is_page_number(b.text):
                out.append(Issue("noise.page_number", "info", b.start, "疑似页码行", one))
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            tail = one[-1:] if one else ""
            if (nxt is not None and nxt.kind == "paragraph" and tail and tail not in TERMINAL
                    and H.has_cjk_tail(one) and re.match(r"^[\u4e00-\u9fff]", nxt.text)
                    and not H.detect_unit(nxt.text) and len(one) > 40):
                out.append(Issue("text.split_paragraph", "info", b.start,
                                 f"段落以非终止符结尾且下一段（L{nxt.start}）直接续接，疑似跨页断段",
                                 _excerpt(one[-40:], 40)))
        elif b.kind == "table" and "<table" in b.text.lower():
            opens = len(re.findall(r"<t[dh][\s>]", b.text, re.I))
            closes = len(re.findall(r"</t[dh]>", b.text, re.I))
            if opens != closes:
                out.append(Issue("table.cells", "warn", b.start, f"表格单元格标签不配对 ({opens}/{closes})",
                                 _excerpt(b.text)))
        elif b.kind == "heading":
            info = H.classify_heading(b.heading)
            key = re.sub(r"\s+", "", info.title)
            heading_titles[key] += 1
            heading_line.setdefault(key, b.start)
            if len(info.title) > 60:
                out.append(Issue("heading.long", "warn", b.start,
                                 f"标题过长（{len(info.title)} 字），可能是正文被误识别为标题", _excerpt(info.title)))
            if H.detect_unit(b.heading) or H.detect_caption(b.heading):
                out.append(Issue("heading.is_unit", "info", b.start, "标题实际是定理/例题/图注等，规划时应降级",
                                 _excerpt(info.title)))
            if info.kind == "numbered" and "." in info.number:
                parent, _, last = info.number.rpartition(".")
                if last.isdigit():
                    prev = last_num.get(parent)
                    if prev is not None and int(last) > prev + 1:
                        out.append(Issue("heading.numbering_gap", "warn", b.start,
                                         f"编号跳跃: {parent}.{prev} → {info.number}，可能漏识别标题",
                                         _excerpt(info.title)))
                    last_num[parent] = int(last)

        if base is not None:
            for ref in image_refs(b.text):
                if not (base / ref).exists():
                    out.append(Issue("image.missing", "warn", b.start, f"图片文件不存在: {ref}", ref))

    for t, c in para_counter.items():
        if c >= 5 and not H.is_page_number(t) and not H.detect_unit(t):
            out.append(Issue("noise.repeated_line", "info", para_first_line[t],
                             f"短行重复出现 {c} 次，疑似页眉/页脚", t))
    for t, c in heading_titles.items():
        if c >= 3 and not H.special_role(t):
            out.append(Issue("heading.duplicate", "warn", heading_line[t],
                             f"相同标题出现 {c} 次，可能是页眉被识别为标题", t))
    if blocks:
        levels = {b.level for b in blocks if b.kind == "heading"}
        if len(levels) == 1:
            out.append(Issue("heading.flat", "info", 1,
                             f"所有标题都是 {'#' * levels.pop()} 同一级（MinerU 常见），层级需按编号重建"))
    out.sort(key=lambda x: (x.line, x.code))
    return out


SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def issues_markdown(issues: list[Issue], per_code: int = 15) -> str:
    by_code: dict[str, list[Issue]] = defaultdict(list)
    for it in issues:
        by_code[it.code].append(it)
    codes = sorted(by_code, key=lambda c: (SEVERITY_ORDER[by_code[c][0].severity], c))
    sev = Counter(i.severity for i in issues)
    out = ["# 校验报告", "",
           f"共 {len(issues)} 条：error {sev.get('error', 0)} / warn {sev.get('warn', 0)} / info {sev.get('info', 0)}",
           "", "| code | 级别 | 数量 |", "|---|---|---|"]
    out += [f"| {c} | {by_code[c][0].severity} | {len(by_code[c])} |" for c in codes]
    for c in codes:
        items = by_code[c]
        out += ["", f"## {c} ({len(items)})", ""]
        for it in items[:per_code]:
            out.append(f"- L{it.line}: {it.msg}" + (f" — `{it.text}`" if it.text else ""))
        if len(items) > per_code:
            out.append(f"- …其余 {len(items) - per_code} 条见 issues.json")
    return "\n".join(out) + "\n"
