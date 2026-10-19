"""`bookdb` command line.

Import:  prepare → (agent reviews/edits plan.json) → check / preview → ingest → verify
Query :  books · toc · search · get · read · lookup · units · stats · mcp
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from . import build as B
from . import db as D
from . import fmt
from . import search as S
from . import workspace as W
from .textutil import slugify


def _emit(args, data, render) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        print(render(data))


def _ro(args):
    return D.connect(args.db, readonly=True)


# ---------------------------------------------------------------- import side

def cmd_prepare(args) -> int:
    files = W.collect_markdown(args.paths)
    if not files:
        print("没有找到 .md 文件", file=sys.stderr)
        return 2
    work_root = Path(args.work)
    used: dict[str, int] = {}
    results = []
    for md in files:
        name = slugify(md.stem)
        used[name] = used.get(name, 0) + 1
        if used[name] > 1:
            name = f"{name}-{used[name]}"
        cl = Path(args.content_list) if args.content_list and len(files) == 1 else None
        results.append(W.prepare_one(md, work_root, force=args.force, content_list=cl, name=name))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0
    for r in results:
        print(f"■ {r['source']}")
        print(f"  工作目录: {r['work']}{'（保留了已有 plan.json）' if r['plan_kept'] else ''}")
        print(f"  {r['lines']} 行 / {r['chars']} 字符 / {r['blocks']} 块；草拟章节 {r['sections']}，"
              f"降级伪标题 {r['demoted']}，噪声行 {r['noise_lines']}")
        print(f"  页码映射: {r['content_list'] or '无 content_list.json'}"
              f"{'（匹配 %d 块）' % r['pages_matched'] if r['content_list'] else ''}")
        print(f"  校验: {r['issues']}")
        print(f"  → 审阅 {r['work']}/outline.md 与 issues.md，编辑 plan.json 后运行 check")
    return 0


def cmd_outline(args) -> int:
    ld = W.load(args.work)
    print(W.refresh_outline(ld).read_text(encoding="utf-8"))
    return 0


def _parse_range(s: str) -> tuple[int, int]:
    a, _, b = s.partition("-")
    lo = int(a)
    hi = int(b) if b else lo
    return lo, hi


def cmd_show(args) -> int:
    p = Path(args.target)
    if p.suffix.lower() == ".md":
        text = p.read_text(encoding="utf-8")
        from .textutil import split_lines
        lines = split_lines(text)
    else:
        lines = W.load(p).lines
    lo, hi = _parse_range(args.lines)
    lo = max(1, lo - args.context)
    hi = min(len(lines), hi + args.context)
    for i in range(lo, hi + 1):
        print(f"{i:>6}| {lines[i - 1].rstrip(chr(10))}")
    return 0


def cmd_sample(args) -> int:
    ld = W.load(args.work)
    pool = [b for b in ld.blocks if args.kind == "any" or b.kind == args.kind]
    if args.kind == "any":
        pool = [b for b in pool if b.kind != "heading" and len(b.text) > 30]
    rng = random.Random(args.seed)
    picks = sorted(rng.sample(pool, min(args.n, len(pool))), key=lambda b: b.start)
    for b in picks:
        page = f" p.{b.page}" if b.page else ""
        print(f"----- L{b.start}-{b.end} [{b.kind}]{page}")
        for k in range(b.start, b.end + 1):
            print(f"{k:>6}| {ld.lines[k - 1].rstrip(chr(10))}")
    return 0


def _build(work) -> tuple[W.Loaded, B.BuildResult]:
    ld = W.load(work)
    return ld, B.build(ld.plan, ld.lines, ld.blocks)


def cmd_check(args) -> int:
    rc = 0
    for work in args.work:
        ld, res = _build(work)
        st = B.stats(res)
        W.refresh_outline(ld)
        if args.json:
            print(json.dumps({"work": str(ld.work), "errors": res.errors, "warnings": res.warnings,
                              "stats": st, "status": ld.plan.get("status")}, ensure_ascii=False, indent=1))
        else:
            print(f"■ {ld.work}  status={ld.plan.get('status')}")
            for e in res.errors:
                print(f"  ✗ {e}")
            for w in res.warnings[:args.max_warnings]:
                print(f"  ! {w}")
            if len(res.warnings) > args.max_warnings:
                print(f"  ! …其余 {len(res.warnings) - args.max_warnings} 条警告")
            if not res.errors:
                print(f"  ✓ 无损校验通过；{st['sections']} 章节 / {st['chunks']} 块；"
                      f"块大小 min {st['chunk_chars']['min']} / 中位 {st['chunk_chars']['median']} / "
                      f"max {st['chunk_chars']['max']}")
                print(f"  类型: {st['chunk_types']}")
                print(f"  标签: {st['labels']}")
                print(f"  角色: {st['roles']}")
        rc = rc or (1 if res.errors else 0)
    return rc


def cmd_preview(args) -> int:
    _, res = _build(args.work)
    if res.errors:
        print("\n".join("✗ " + e for e in res.errors))
        return 1
    print(B.preview(res, args.section, args.line, args.limit))
    return 0


def cmd_ingest(args) -> int:
    conn = D.connect(args.db)
    rc = 0
    for work in args.work:
        ld, res = _build(work)
        if res.errors:
            print(f"✗ {ld.work}: plan 有 {len(res.errors)} 个错误，先运行 check 修复", file=sys.stderr)
            rc = 1
            continue
        if ld.plan.get("status") != "reviewed" and not args.allow_draft:
            print(f"✗ {ld.work}: plan.status 不是 \"reviewed\"。请先完成模型审阅（见 SKILL.md），"
                  "或使用 --allow-draft 强制导入", file=sys.stderr)
            rc = 1
            continue
        info = D.ingest(conn, ld.plan, res, ld.data, replace=args.replace, copy_images=not args.no_copy_images)
        ver = D.verify(conn, info["book"])[0]
        print(f"✓ 已导入 {info['book']}: {info['sections']} 章节 / {info['chunks']} 块 / "
              f"{info['labels']} 标签 / {info['assets']} 图片；无损={'是' if ver['lossless'] else '否'}"
              f"  → {D.db_file(conn)}")
    return rc


def cmd_verify(args) -> int:
    conn = _ro(args)
    rows = D.verify(conn, args.book)
    _emit(args, rows, lambda rs: "\n".join(
        f"{'✓' if r['lossless'] else '✗'} {r['book']}  无损={r['lossless']}  源文件={r['source_file']}"
        for r in rs) or "（无书籍）")
    return 0 if all(r["lossless"] for r in rows) else 1


def cmd_export(args) -> int:
    conn = _ro(args)
    text = D.export_book(conn, S.resolve_book(conn, args.book)["id"], corrected=args.corrected)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def cmd_remove(args) -> int:
    conn = D.connect(args.db)
    ok = D.remove_book(conn, args.book)
    conn.commit()
    print("已删除" if ok else "不存在", args.book)
    return 0 if ok else 1


# ---------------------------------------------------------------- query side

def cmd_books(args) -> int:
    _emit(args, S.list_books(_ro(args)), fmt.books)
    return 0


def cmd_toc(args) -> int:
    _emit(args, S.toc(_ro(args), args.book, args.depth, args.section), fmt.toc)
    return 0


def cmd_search(args) -> int:
    d = S.search(_ro(args), " ".join(args.query), books=args.book, types=args.type, section=args.section,
                 limit=args.limit, mode=args.mode, subject=args.subject, field=args.field,
                 all_roles=args.all_roles)
    _emit(args, d, fmt.search)
    return 0


def _cid(s: str) -> int:
    return int(str(s).lstrip("cC#"))


def cmd_get(args) -> int:
    conn = _ro(args)
    ids = [_cid(x) for x in args.chunk_id]
    if len(ids) == 1:
        _emit(args, S.get_chunk(conn, ids[0], args.context, args.raw), fmt.chunk)
    else:
        ds = [S.get_chunk(conn, i, args.context, args.raw) for i in ids]
        if args.json:
            print(json.dumps(ds, ensure_ascii=False, indent=1))
        else:
            print("\n\n".join(fmt.chunk(d) for d in ds))
    return 0


def cmd_read(args) -> int:
    d = S.read_section(_ro(args), args.book, args.section, args.max_chars,
                       _cid(args.from_chunk) if args.from_chunk else None, args.raw, not args.no_children)
    _emit(args, d, fmt.section)
    return 0


def cmd_lookup(args) -> int:
    d = S.lookup(_ro(args), " ".join(args.ref), args.book, args.chapter, args.limit, not args.brief)
    _emit(args, d, fmt.lookup)
    return 0


def cmd_units(args) -> int:
    _emit(args, S.list_units(_ro(args), args.book, args.type, args.section, args.limit), fmt.units)
    return 0


def cmd_stats(args) -> int:
    conn = _ro(args)
    d = S.stats(conn)
    d["db"] = str(D.db_file(conn))
    _emit(args, d, lambda x: json.dumps(x, ensure_ascii=False, indent=1))
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import run
    run(args.db)
    return 0


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help=f"数据库路径（默认 $BOOK_DB 或 {D.default_db_path()}）")
    common.add_argument("--json", action="store_true", help="输出 JSON")

    p = argparse.ArgumentParser(prog="bookdb", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, parents=[common], help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("prepare", cmd_prepare, "解析 MinerU markdown，生成校验报告、大纲与草拟 plan.json")
    sp.add_argument("paths", nargs="+", help=".md 文件或包含 .md 的目录（可多个）")
    sp.add_argument("--work", default="bookdb-work", help="工作目录根（默认 ./bookdb-work）")
    sp.add_argument("--content-list", help="MinerU *_content_list.json（单文件时可显式指定；默认自动查找）")
    sp.add_argument("--force", action="store_true", help="覆盖已有 plan.json")

    sp = add("outline", cmd_outline, "按当前 plan.json 重新生成并打印 outline.md")
    sp.add_argument("work")

    sp = add("show", cmd_show, "带行号显示源文件片段")
    sp.add_argument("target", help="工作目录或 .md 文件")
    sp.add_argument("--lines", "-l", required=True, help="如 120-180 或 120")
    sp.add_argument("--context", "-C", type=int, default=0)

    sp = add("sample", cmd_sample, "随机抽样若干块用于质量抽检")
    sp.add_argument("work")
    sp.add_argument("-n", type=int, default=8)
    sp.add_argument("--kind", default="any", choices=["any", "paragraph", "math", "table", "image", "heading"])
    sp.add_argument("--seed", type=int, default=None)

    sp = add("check", cmd_check, "按 plan 试构建（不写库）：报告错误/警告/统计并验证无损")
    sp.add_argument("work", nargs="+")
    sp.add_argument("--max-warnings", type=int, default=30)

    sp = add("preview", cmd_preview, "预览某章节的分块结果")
    sp.add_argument("work")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--section", "-s", help="章节编号或标题片段")
    g.add_argument("--line", type=int, help="包含该行的章节")
    sp.add_argument("--limit", type=int, default=80)

    sp = add("ingest", cmd_ingest, "将审阅完成的 plan 写入数据库")
    sp.add_argument("work", nargs="+")
    sp.add_argument("--replace", action="store_true", help="同 id 书籍已存在时覆盖")
    sp.add_argument("--allow-draft", action="store_true", help="允许导入 status 非 reviewed 的 plan")
    sp.add_argument("--no-copy-images", action="store_true", help="不复制图片，只记录原路径")

    sp = add("verify", cmd_verify, "从数据库重建原文并与源文件哈希比对")
    sp.add_argument("book", nargs="?")

    sp = add("export", cmd_export, "导出整本书（默认原文，--corrected 为修正层）")
    sp.add_argument("book")
    sp.add_argument("--corrected", action="store_true")
    sp.add_argument("-o", "--output")

    sp = add("remove", cmd_remove, "从数据库删除一本书")
    sp.add_argument("book")

    add("books", cmd_books, "列出已导入书籍")

    sp = add("toc", cmd_toc, "查看目录（章节 id 形如 s123）")
    sp.add_argument("book")
    sp.add_argument("--depth", "-d", type=int, default=3)
    sp.add_argument("--section", "-s", help="只看某章节的子目录")

    sp = add("search", cmd_search, "全文检索（中文二元组 BM25 + trigram 精确子串）")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--book", "-b", action="append", help="限定书籍（可多次）")
    sp.add_argument("--type", "-t", action="append",
                    help="限定块类型：definition/theorem/example/exercise/proof/…（可多次）")
    sp.add_argument("--section", "-s", help="限定章节（需同时指定唯一 --book）")
    sp.add_argument("--subject")
    sp.add_argument("--field")
    sp.add_argument("--limit", "-k", type=int, default=8)
    sp.add_argument("--mode", default="auto", choices=["auto", "bm25", "exact"])
    sp.add_argument("--all-roles", action="store_true", help="包含目录/索引中的命中")

    sp = add("get", cmd_get, "读取 chunk 全文（可带上下文）")
    sp.add_argument("chunk_id", nargs="+", help="如 123 或 c123")
    sp.add_argument("--context", "-C", type=int, default=0, help="前后各附带 N 个块")
    sp.add_argument("--raw", action="store_true", help="显示未修正的原文")

    sp = add("read", cmd_read, "读取整个章节（超长时分页）")
    sp.add_argument("book")
    sp.add_argument("section", help="章节编号(2.3)/第2章/§2.3/标题片段/s123")
    sp.add_argument("--max-chars", type=int, default=20000)
    sp.add_argument("--from", dest="from_chunk", help="从指定 chunk 继续（分页）")
    sp.add_argument("--raw", action="store_true")
    sp.add_argument("--no-children", action="store_true", help="不含子章节")

    sp = add("lookup", cmd_lookup, "按编号精确定位：定理2.3、例1.2、(2.15)、图3-1、习题4.5、第2章、§2.3")
    sp.add_argument("ref", nargs="+")
    sp.add_argument("--book", "-b", action="append")
    sp.add_argument("--chapter", help="限定章号（区分各章重复的“例1”）")
    sp.add_argument("--limit", type=int, default=5)
    sp.add_argument("--brief", action="store_true", help="不附带全文")

    sp = add("units", cmd_units, "列出某书（某章节）的定义/定理/例题等")
    sp.add_argument("book")
    sp.add_argument("--type", "-t", action="append")
    sp.add_argument("--section", "-s")
    sp.add_argument("--limit", type=int, default=200)

    add("stats", cmd_stats, "数据库统计")
    add("mcp", cmd_mcp, "以 stdio MCP 服务器运行（需 --extra mcp）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except (S.NotFound, ValueError, FileNotFoundError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
