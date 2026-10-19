"""Heuristics for textbook structure: heading numbering, labelled units, captions,
equation tags and page-noise detection. These only *propose*; the agent's plan
is authoritative."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .textutil import CJK_RE, CN_NUM_CLASS, cn_to_int, nfkc, norm_number, roman_to_int

CN = f"[0-9{CN_NUM_CLASS}]+"
NUM = r"\d+(?:\s*[.．\-－—–]\s*\d+)*[a-zA-Z]?"
DOT_LEADER_RE = re.compile(r"(?:[.…·．。]\s*){3,}\s*\d+\s*$|\s\d{1,4}\s*$")

ROLES = ["front_matter", "toc", "preface", "body", "summary", "exercises", "answers",
         "appendix", "references", "index", "back_matter"]

# (role, keywords) — matched at the start of a normalised heading title.
SPECIAL_HEADINGS = [
    ("toc", ["目录", "目次", "contents", "tableofcontents"]),
    ("preface", ["前言", "序言", "序", "再版前言", "第二版前言", "出版说明", "编者的话",
                 "preface", "foreword"]),
    ("exercises", ["习题与思考题", "复习思考题", "思考题与习题", "思考题", "练习题", "习题", "练习",
                   "exercises", "problems", "exercise"]),
    ("answers", ["部分习题答案", "习题参考答案", "习题答案", "参考答案", "答案与提示", "答案",
                 "answerstoselected", "answers", "solutions"]),
    ("summary", ["本章小结", "本章总结", "内容提要", "小结", "总结", "summary"]),
    ("references", ["参考文献", "参考书目", "主要参考书", "references", "bibliography",
                    "furtherreading"]),
    ("index", ["名词索引", "术语索引", "索引", "index"]),
    ("appendix", ["附录", "appendix", "appendices"]),
    ("back_matter", ["后记", "跋", "afterword"]),
]
TOP_LEVEL_ROLES = {"toc", "preface", "references", "index", "appendix", "back_matter"}


@dataclass
class HeadingInfo:
    kind: str           # part | chapter | numbered | cn_enum | cn_paren | special | plain
    depth: int | None   # absolute depth (chapter=1) when known
    number: str
    title: str
    role: str | None = None
    conf: str = "high"


_HEAD_PATTERNS = [
    ("part", re.compile(rf"^第\s*({CN})\s*(?:篇|部分|编|卷)\s*(.*)$")),
    ("part", re.compile(r"^(?:Part|PART)\s+(\d+|[IVXLC]+)\b[\s.:：]*(.*)$")),
    ("chapter", re.compile(rf"^第\s*({CN})\s*(?:章|讲)\s*(.*)$")),
    ("chapter", re.compile(r"^(?:Chapter|CHAPTER)\s+(\d+|[IVXLC]+)\b[\s.:：]*(.*)$")),
    ("section", re.compile(rf"^第\s*({CN})\s*节\s*(.*)$")),
    ("numbered", re.compile(r"^§?\s*([A-Z]?\d*(?:\s*[.．]\s*\d+)+)\s*[.．]?\s*(.*)$")),
    ("sect_mark", re.compile(r"^§\s*(\d+)\s*[.．]?\s*(.*)$")),
    ("cn_enum", re.compile(r"^([一二三四五六七八九十]+)\s*[、.．]\s*(.*)$")),
    ("cn_paren", re.compile(r"^[（(]\s*([一二三四五六七八九十]+)\s*[)）]\s*(.*)$")),
    ("arabic", re.compile(r"^(\d{1,2})\s*[.．、]?\s+([^\d\s].*)$")),
]


def clean_heading(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^\*\*(.*)\*\*$", r"\1", t)
    t = re.sub(r"^__(.*)__$", r"\1", t)
    return t.strip()


def special_role(title: str) -> str | None:
    norm = nfkc(title).lower()
    t = re.sub(r"[\s*\u3000]", "", norm)
    words = re.findall(r"[a-z]+", norm) if re.match(r"^[\s*]*[a-z]", norm) else []
    prefixes = {"".join(words[:i]) for i in range(1, min(len(words), 4) + 1)}
    for role, keys in SPECIAL_HEADINGS:
        for k in keys:
            if k.isascii():
                if k in prefixes:
                    return role
            elif t.startswith(k):
                rest = t[len(k):]
                if (not rest or role == "appendix"
                        or re.fullmatch(rf"[\dA-Za-z{CN_NUM_CLASS}.．\-—:：()（）、]*", rest)):
                    return role
    return None


def classify_heading(raw: str) -> HeadingInfo:
    title = clean_heading(raw)
    t = nfkc(title).lstrip("*").strip()  # leading * marks optional sections
    role = special_role(t)
    if role:
        num = ""
        m = re.search(r"([A-Z]|\d+(?:[.．]\d+)*)\s*$", t)
        if role == "appendix":
            m2 = re.match(r"^(?:附录|appendix)\s*([A-Z]|\d+)", t, re.I)
            num = m2.group(1).upper() if m2 else ""
        elif role == "exercises" and m:
            num = norm_number(m.group(1))
        depth = 1 if role in TOP_LEVEL_ROLES else None
        return HeadingInfo("special", depth, num, title, role)
    for kind, rx in _HEAD_PATTERNS:
        m = rx.match(t)
        if not m:
            continue
        raw_num = m.group(1)
        if kind in ("part", "chapter", "section"):
            v = cn_to_int(raw_num)
            if v is None:
                v = roman_to_int(raw_num)
            if v is None:
                continue
            depth = {"part": 0, "chapter": 1, "section": 2}[kind]
            return HeadingInfo(kind, depth, str(v), title)
        if kind == "numbered":
            num = norm_number(raw_num)
            if not num or num.startswith("."):
                continue
            if DOT_LEADER_RE.search(t) and len(t) > 12:
                return HeadingInfo("toc_line", None, num, title, "toc", "low")
            return HeadingInfo("numbered", num.count(".") + 1, num, title)
        if kind == "sect_mark":
            return HeadingInfo("numbered", 2, raw_num, title, conf="medium")
        if kind == "cn_enum":
            return HeadingInfo("cn_enum", None, str(cn_to_int(raw_num)), title, conf="medium")
        if kind == "cn_paren":
            return HeadingInfo("cn_paren", None, str(cn_to_int(raw_num)), title, conf="medium")
        if kind == "arabic":
            return HeadingInfo("chapter", 1, raw_num, title, conf="low")
    return HeadingInfo("plain", None, "", title, conf="low")


# ------------------------------------------------------------------ units

UNIT_TYPES = ["definition", "theorem", "lemma", "corollary", "proposition", "axiom", "law",
              "example", "exercise", "solution", "proof", "remark", "algorithm"]
STATEMENT_TYPES = {"definition", "theorem", "lemma", "corollary", "proposition", "axiom", "law"}
CHUNK_TYPES = ["text", *UNIT_TYPES, "summary", "toc", "index", "reference", "front_matter"]

_UNIT_KW = [
    ("definition", "定义", r"Definition|Defn?\."),
    ("theorem", "定理", r"Theorem|Thm\."),
    ("lemma", "引理", r"Lemma"),
    ("corollary", "推论", r"Corollary|Cor\."),
    ("proposition", "命题", r"Proposition|Prop\."),
    ("axiom", "公理|公设", r"Axiom|Postulate"),
    ("law", "定律", r"Law"),
    ("example", "例题|例", r"Example|EXAMPLE|Ex\."),
    ("exercise", "习题|练习", r"Exercise|Problem"),
    ("remark", "注意|注记|注|说明", r"Remark|Note"),
    ("algorithm", "算法", r"Algorithm"),
]
_PRE = r"^\s*(?:\*\*|__)?\s*[【\[]?\s*\*?"
_TITLE = r"(?:\s*[（(](?P<title>[^（）()]{1,40})[)）])?"
_POST = r"\s*[】\]]?\s*(?:\*\*|__)?"

_UNIT_RES = []
for _typ, _cn, _en in _UNIT_KW:
    _UNIT_RES.append((_typ, re.compile(
        rf"{_PRE}(?P<kw>{_cn})\s*(?P<num>{NUM})?{_TITLE}{_POST}(?P<sep>[:：.．。]|\s|$)?")))
    _UNIT_RES.append((_typ, re.compile(
        rf"{_PRE}(?P<kw>{_en})\s*(?P<num>{NUM})?{_TITLE}{_POST}(?P<sep>[:：.．]|\s|$)")))
_PROOF_RE = re.compile(rf"{_PRE}(?P<kw>证明|证|Proof|PROOF){_POST}(?P<sep>[:：.．]|\s|$)")
_SOLUTION_RE = re.compile(rf"{_PRE}(?P<kw>解答|解|答|Solution|SOLUTION|Answer){_POST}(?P<sep>[:：.．]|\s|$)")
END_MARK_RE = re.compile(r"(□|∎|■|\\square|\\blacksquare|\\Box|证毕|Q\.?E\.?D\.?)\s*\$?\s*$")
EXERCISE_ITEM_RE = re.compile(r"^\s*(?:\*\*)?\s*(\d+(?:\s*[.．\-]\s*\d+)*)\s*(?:\*\*)?\s*[.．、)）]\s*\S")
CAPTION_RE = re.compile(rf"^\s*(?:\*\*)?\s*(图|Fig\.?|Figure|FIGURE|表|Table|TABLE)\s*({NUM})")
CONTINUATION_RE = re.compile(r"^\s*(其中|式中|这里|此处|即|则|得|并且|且|where|with|which|and)(?![a-z])")
# words that turn "例1中…" / "定理2的…" into a cross-reference rather than a unit start
_REF_FOLLOW = set("中的所给可已里和与及或是为对在有就时后")


@dataclass
class UnitHit:
    type: str
    number: str
    title: str
    label: str


def detect_unit(text: str) -> UnitHit | None:
    first = nfkc(text.split("\n", 1)[0])
    if len(first) < 2:
        return None
    for typ, rx in _UNIT_RES:
        m = rx.match(first)
        if not m:
            continue
        num, sep, kw = m.group("num"), m.group("sep"), m.group("kw")
        bold = first.lstrip().startswith(("**", "__", "【", "["))
        if sep is None:
            nxt = first[m.end():m.end() + 1]
            if not num or nxt in _REF_FOLLOW or not CJK_RE.match(nxt):
                continue
        elif not num and sep not in (":", "：", ".", "．") and not (bold and sep in ("", " ")):
            continue
        if typ == "remark" and kw == "说明" and not num:
            continue
        title = (m.group("title") or "").strip()
        number = norm_number(num) if num else ""
        label = kw + (" " + number if number else "") + (f"（{title}）" if title else "")
        return UnitHit(typ, number, title, label)
    for typ, rx in (("proof", _PROOF_RE), ("solution", _SOLUTION_RE)):
        m = rx.match(first)
        if m:
            return UnitHit(typ, "", "", m.group("kw"))
    return None


def detect_caption(text: str) -> tuple[str, str, str] | None:
    m = CAPTION_RE.match(nfkc(text.split("\n", 1)[0]))
    if not m:
        return None
    kind = "figure" if m.group(1).lower().startswith(("图", "fig")) else "table"
    return kind, norm_number(m.group(2)), text.split("\n", 1)[0].strip()[:120]


def detect_exercise_item(text: str) -> str | None:
    m = EXERCISE_ITEM_RE.match(nfkc(text))
    return norm_number(m.group(1)) if m else None


# ---------------------------------------------------------------- equations

_TAG_RE = re.compile(r"\\tag\*?\s*\{\s*\(?\s*([^{}()]+?)\s*\)?\s*\}")
_TRAIL_TAG_RE = re.compile(
    r"(?:\\q?quad|\\[,;:!]|~|\s)*[（(]\s*(\d+(?:\s*[.．\-－—–]\s*\d+)+[a-z']?|[A-Z]\s*[.．\-]\s*\d+)\s*[)）]\s*$")
_QUAD_TAG_RE = re.compile(r"\\q?quad\s*[（(]\s*(\d+[a-z']?)\s*[)）]\s*$")
TAG_LINE_RE = re.compile(r"^\s*[（(]\s*(\d+(?:\s*[.．\-－—–]\s*\d+)*[a-z']?)\s*[)）]\s*$")


def math_body(text: str) -> str:
    t = text.strip()
    for o, c in (("$$", "$$"), ("\\[", "\\]")):
        if t.startswith(o):
            t = t[len(o):]
            k = t.rfind(c)
            if k >= 0:
                t = t[:k]
            break
    return t.strip()


def equation_tags(math_text: str) -> list[str]:
    body = math_body(math_text)
    tags = [norm_number(m.group(1)) for m in _TAG_RE.finditer(body)]
    if not tags:
        m = _TRAIL_TAG_RE.search(body) or _QUAD_TAG_RE.search(body)
        if m:
            tags.append(norm_number(m.group(1)))
    # also "(2.15)" sitting just outside the closing $$ on the same line
    tail = math_text.strip()
    if not tags and tail.endswith(")"):
        m = TAG_LINE_RE.search(tail.rsplit("$$", 1)[-1]) if "$$" in tail else None
        if m:
            tags.append(norm_number(m.group(1)))
    return [t for t in tags if t]


# ------------------------------------------------------------------- noise

PAGE_NUM_RE = re.compile(r"^\s*(?:[-—–·•]\s*)?(?:第\s*)?\d{1,4}(?:\s*页)?(?:\s*[-—–·•])?\s*$")


def is_page_number(text: str) -> bool:
    return "\n" not in text.strip() and bool(PAGE_NUM_RE.match(nfkc(text)))


def has_cjk_tail(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]$", s))
