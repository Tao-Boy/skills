import json
from pathlib import Path

import pytest

from book_db import build as B
from book_db import db as D
from book_db import heuristics as H
from book_db import plan as P
from book_db import search as S
from book_db import workspace as W
from book_db.cli import main
from book_db.mdparse import parse_blocks
from book_db.textutil import norm_number, query_terms, split_lines

FIXTURE = Path(__file__).parent / "fixtures" / "sample" / "electrodynamics.md"


@pytest.fixture()
def work(tmp_path):
    info = W.prepare_one(FIXTURE.resolve(), tmp_path / "work")
    return Path(info["work"])


def _edit(work: Path, fn):
    plan = P.load_plan(work)
    fn(plan)
    P.save_plan(plan, work / "plan.json")


# ---------------------------------------------------------------- parsing

def test_split_lines_lossless_with_crlf_and_no_trailing_newline():
    text = "a\r\n\r\n$$\r\nx\r\n$$\r\nlast"
    lines = split_lines(text)
    assert "".join(lines) == text
    blocks = parse_blocks(lines)
    assert [b.kind for b in blocks] == ["paragraph", "math", "paragraph"]
    assert blocks[1].start == 3 and blocks[1].end == 5


def test_unclosed_math_is_flagged_and_bounded():
    lines = split_lines("$$\nE = mc^2\n\nnext paragraph\n")
    blocks = parse_blocks(lines)
    assert blocks[0].kind == "math" and blocks[0].unclosed and blocks[0].end == 2
    assert blocks[1].kind == "paragraph"


def test_heuristics():
    assert H.classify_heading("第三章 静电场").depth == 1
    assert H.classify_heading("2.3.1 电势").depth == 3
    assert H.classify_heading("习题 2").role == "exercises"
    assert H.classify_heading("Indexing").role is None
    u = H.detect_unit("**定理 2.3（高斯定理）** 设 ...")
    assert (u.type, u.number, u.title) == ("theorem", "2.3", "高斯定理")
    assert H.detect_unit("例如，我们考虑") is None
    assert H.detect_unit("例1中的结论") is None
    assert H.detect_unit("解得 x=1") is None
    assert H.detect_unit("证明：显然").type == "proof"
    assert H.equation_tags("$$\na = b \\tag{2-15}\n$$") == ["2.15"]
    assert H.equation_tags("$$ a=b \\qquad (3.1) $$") == ["3.1"]
    assert norm_number("二．3") == "2.3"
    assert query_terms("高斯定理") == ['"高斯"', '"斯定"', '"定理"']


# ---------------------------------------------------------------- draft & build

def test_draft_plan_structure(work):
    plan = P.load_plan(work)
    titles = [s["title"] for s in plan["sections"]]
    assert titles[:4] == ["电动力学简明教程", "目录", "前言", "第一章 静电场"]
    assert {d["line"] for d in plan["demoted"]} == {5, 7, 9, 11}  # TOC entries
    levels = {s["title"]: s["level"] for s in plan["sections"]}
    assert levels["1.1 库仑定律"] == 2 and levels["第二章 静磁场"] == 1
    roles = {s["title"]: s["role"] for s in plan["sections"]}
    assert roles["习题"] == "exercises" and roles["参考文献"] == "references"
    assert 35 in plan["noise_lines"] and 33 in plan["noise_lines"]
    assert (work / "outline.md").exists() and (work / "issues.md").exists()


def test_issues_detected(work):
    codes = {i["code"] for i in json.loads((work / "issues.json").read_text(encoding="utf-8"))}
    assert {"image.missing", "math.braces", "heading.numbering_gap", "noise.page_number",
            "noise.repeated_line", "text.split_paragraph", "heading.flat"} <= codes


def test_build_lossless_and_units(work):
    ld = W.load(work)
    res = B.build(ld.plan, ld.lines, ld.blocks)
    assert not res.errors
    assert "".join(c.raw for c in res.chunks) == FIXTURE.read_text(encoding="utf-8")
    labels = {(lb.kind, lb.key) for c in res.chunks for lb in c.labels}
    assert {("theorem", "1.2"), ("definition", "1.1"), ("example", "1.1"), ("equation", "1.3"),
            ("equation", "1.2"), ("figure", "2.1"), ("table", "1.1"), ("exercise", "1.2")} <= labels
    proof = next(c for c in res.chunks if c.type == "proof")
    assert res.chunks[proof.ref].type == "theorem"
    example = next(c for c in res.chunks if c.type == "example")
    assert "解 由对称性" in example.raw  # solution kept with its example
    # noise lines are blanked only in the corrected layer
    first = next(c for c in res.chunks if c.start <= 35 <= c.end)
    assert "\n12\n" in first.raw and "\n12\n" not in first.content


def test_fixes_units_and_errors(work):
    def edit(plan):
        plan["fixes"].append({"line": 91, "find": "varepsilon_0 r$", "replace": "varepsilon_0 r}$",
                              "reason": "补全花括号"})
        plan["units"].append({"line": 121, "type": None})
    _edit(work, edit)
    ld = W.load(work)
    res = B.build(ld.plan, ld.lines, ld.blocks)
    assert not res.errors
    ex2 = next(c for c in res.chunks if c.start <= 91 <= c.end)
    assert "r}$" in ex2.content and "r}$" not in ex2.raw

    _edit(work, lambda p: p["fixes"].append({"line": 91, "find": "不存在的文本", "replace": "x"}))
    _edit(work, lambda p: p["sections"].append({"line": 130, "level": 1, "title": "bad"}))
    ld = W.load(work)
    res = B.build(ld.plan, ld.lines, ld.blocks)
    assert any("找不到" in e for e in res.errors)


def test_source_must_not_change(work, tmp_path):
    src = tmp_path / "copy.md"
    src.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    info = W.prepare_one(src, tmp_path / "w2")
    src.write_text(src.read_text(encoding="utf-8") + "\nextra\n", encoding="utf-8")
    with pytest.raises(ValueError):
        W.load(info["work"])


# ---------------------------------------------------------------- db & queries

@pytest.fixture()
def conn(work, tmp_path):
    _edit(work, lambda p: (p.__setitem__("status", "reviewed"),
                           p["book"].update(subject="physics", field="电动力学")))
    db = tmp_path / "kb.db"
    assert main(["ingest", str(work), "--db", str(db)]) == 0
    c = D.connect(db, readonly=True)
    yield c
    c.close()


def test_ingest_refuses_draft(work, tmp_path):
    assert main(["ingest", str(work), "--db", str(tmp_path / "x.db")]) == 1


def test_verify_and_export(conn):
    assert D.verify(conn)[0]["lossless"]
    assert D.export_book(conn, "electrodynamics") == FIXTURE.read_text(encoding="utf-8")


def test_search_modes(conn):
    r = S.search(conn, "高斯定理", limit=5)
    assert r["results"][0]["type"] == "theorem"
    r = S.search(conn, "\\nabla")
    assert r["mode"] == "exact" and r["count"] == 1
    r = S.search(conn, "电场", types=["例题"])
    assert r["count"] >= 1 and all(x["type"] == "example" for x in r["results"])
    r = S.search(conn, "电场", books="electrodynamics", section="2")
    assert all(x["path"].startswith("第二章") for x in r["results"])
    assert S.search(conn, "目录")["count"] == 0 or all(
        "目录" != x["path"] for x in S.search(conn, "目录")["results"])


def test_lookup_and_read(conn):
    m = S.lookup(conn, "定理1.2")["matches"]
    assert m and m[0]["label"].startswith("定理 1.2")
    eq = S.lookup(conn, "式(1.3)")["matches"][0]
    assert "\\oint_S" in eq["latex"]
    assert S.lookup(conn, "(1.2)")["matches"][0]["kind"] == "equation"
    assert S.lookup(conn, "习题1.2")["matches"][0]["type"] == "exercise"
    assert S.lookup(conn, "图2-1")["matches"][0]["kind"] == "figure"
    sec = S.lookup(conn, "第2章")
    assert not sec["matches"] and sec["sections"][0]["number"] == "2"
    d = S.read_section(conn, "电动力学", "1.2", max_chars=200)
    assert d["truncated"] and d["next_chunk"]
    d2 = S.read_section(conn, "electrodynamics", "1.2", start_chunk=d["next_chunk"])
    assert "证明" in d2["content"]
    toc = S.toc(conn, "electrodynamics", depth=1)
    assert all(s["level"] == 1 for s in toc["sections"])
    units = S.list_units(conn, "electrodynamics", types="theorem")["units"]
    assert len(units) == 2


def test_cli_query_commands(conn, capsys):
    db = str(D.db_file(conn))
    assert main(["search", "库仑定律", "--db", db]) == 0
    assert "库仑定律" in capsys.readouterr().out
    assert main(["lookup", "例2.1", "--db", db, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["matches"][0]["type"] == "example"
    assert main(["get", "c1", "--db", db]) == 0
    assert main(["toc", "nonexistent", "--db", db]) == 2


def test_multiple_fixes_on_same_line_compose(work):
    def edit(plan):
        plan["fixes"] += [{"line": 89, "find": "q_1=2", "replace": "q_1=3", "reason": "t"},
                          {"line": 89, "find": "q_2=-3", "replace": "q_2=-4", "reason": "t"}]
    _edit(work, edit)
    ld = W.load(work)
    res = B.build(ld.plan, ld.lines, ld.blocks)
    assert not res.errors
    c = next(c for c in res.chunks if c.start <= 89 <= c.end)
    assert "q_1=3" in c.content and "q_2=-4" in c.content and "q_1=2" in c.raw
