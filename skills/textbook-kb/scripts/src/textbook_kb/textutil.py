"""Text helpers: lossless line handling, normalisation and CJK-aware tokenisation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

CJK_CLASS = r"㐀-䶿一-鿿豈-﫿"
CJK_RE = re.compile(f"[{CJK_CLASS}]")
TOKEN_RE = re.compile(f"([{CJK_CLASS}]+)|([A-Za-z0-9]+)")

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_NUM_CLASS = "零〇一二三四五六七八九十百千两"


def read_source(path: str | Path) -> tuple[bytes, str]:
    data = Path(path).read_bytes()
    return data, data.decode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_lines(text: str) -> list[str]:
    """Split keeping line endings so that ''.join(result) == text exactly.

    Only '\\n' is a line separator (unlike str.splitlines), so line numbers
    match what editors and `sed -n` show.
    """
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def strip_eol(line: str) -> str:
    return line.rstrip("\n").rstrip("\r")


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def cn_to_int(s: str) -> int | None:
    """Convert Chinese numerals (一, 十二, 二十三, 一百零五) or digits to int."""
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            unit = CN_UNITS[ch]
            section += (num or 1) * unit
            num = 0
        else:
            return None
    return total + section + num


ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def roman_to_int(s: str) -> int | None:
    s = s.upper()
    if not s or any(c not in ROMAN for c in s):
        return None
    total = 0
    for i, c in enumerate(s):
        v = ROMAN[c]
        if i + 1 < len(s) and ROMAN[s[i + 1]] > v:
            total -= v
        else:
            total += v
    return total


NUM_SEP_RE = re.compile(r"\s*[.．\-－—–·]\s*")


def norm_number(num: str) -> str:
    """'2-3' / '2．3' / '2 . 3' -> '2.3'; Chinese numerals converted."""
    num = nfkc(num).strip().strip("()（）")
    parts = [p for p in NUM_SEP_RE.split(num) if p]
    out = []
    for p in parts:
        v = cn_to_int(p)
        out.append(str(v) if v is not None and not p.isdigit() else p)
    return ".".join(out)


def index_tokens(text: str) -> list[str]:
    """CJK runs -> overlapping bigrams (unigram for isolated chars); ASCII -> words."""
    out: list[str] = []
    for m in TOKEN_RE.finditer(nfkc(text)):
        cjk, word = m.groups()
        if cjk:
            if len(cjk) == 1:
                out.append(cjk)
            else:
                out.extend(cjk[i:i + 2] for i in range(len(cjk) - 1))
        else:
            out.append(word.lower())
    return out


def index_text(text: str) -> str:
    return " ".join(index_tokens(text))


def query_terms(query: str) -> list[str]:
    """FTS5 MATCH terms for a free-text query (each term quoted; lone CJK chars as prefix)."""
    terms: list[str] = []
    seen = set()
    for m in TOKEN_RE.finditer(nfkc(query)):
        cjk, word = m.groups()
        if cjk:
            if len(cjk) == 1:
                toks = [f'"{cjk}"*']
            else:
                toks = [f'"{cjk[i:i + 2]}"' for i in range(len(cjk) - 1)]
        else:
            toks = [f'"{word.lower()}"']
        for t in toks:
            if t not in seen:
                seen.add(t)
                terms.append(t)
    return terms


def highlight_terms(query: str) -> list[str]:
    """Literal substrings used to locate snippets (longest first)."""
    q = nfkc(query)
    terms = [m.group(0) for m in TOKEN_RE.finditer(q)]
    extra = []
    for t in terms:
        if CJK_RE.match(t) and len(t) > 2:
            extra.extend(t[i:i + 2] for i in range(len(t) - 1))
    uniq = list(dict.fromkeys(terms + extra))
    return sorted(uniq, key=len, reverse=True)


def make_snippet(text: str, query: str, width: int = 180) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= width:
        return flat
    low = nfkc(flat).lower()
    terms = [t.lower() for t in highlight_terms(query)]
    best_pos, best_score = 0, -1
    for t in terms:
        start, hits = 0, 0
        while hits < 50:
            p = low.find(t, start)
            if p < 0:
                break
            lo = max(0, p - width // 4)
            window = low[lo:lo + width]
            score = sum(len(x) for x in terms if x in window)
            if score > best_score:
                best_score, best_pos = score, lo
            start, hits = p + 1, hits + 1
    snippet = flat[best_pos:best_pos + width]
    return ("…" if best_pos > 0 else "") + snippet + ("…" if best_pos + width < len(flat) else "")


def slugify(s: str) -> str:
    s = nfkc(s).strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(rf"[^\w\-{CJK_CLASS}]", "", s)
    return s.strip("-") or "book"
