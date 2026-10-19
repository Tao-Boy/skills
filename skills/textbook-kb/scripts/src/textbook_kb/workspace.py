"""Per-book working directories: prepare (draft plan + reports) and reload."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import plan as P
from .mdparse import Block, apply_pages, find_content_list, parse_blocks
from .textutil import read_source, sha256_bytes, slugify, split_lines
from .validate import Issue, issues_markdown, validate


@dataclass
class Loaded:
    work: Path
    plan: dict
    data: bytes
    lines: list[str]
    blocks: list[Block]


def collect_markdown(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(x for x in p.rglob("*.md") if x.is_file())
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"不存在: {p}")
    return [x.resolve() for x in dict.fromkeys(out)]


def _summary(issues: list[Issue]) -> str:
    from collections import Counter
    c = Counter(i.severity for i in issues)
    return f"error {c.get('error', 0)} / warn {c.get('warn', 0)} / info {c.get('info', 0)}"


def prepare_one(md: Path, work_root: Path, force: bool = False, content_list: Path | None = None,
                name: str | None = None) -> dict:
    data, text = read_source(md)
    lines = split_lines(text)
    blocks = parse_blocks(lines)
    cl = content_list or find_content_list(md)
    matched = apply_pages(blocks, cl)
    issues = validate(lines, blocks, md)
    work = work_root / (name or slugify(md.stem))
    plan_path = work / "plan.json"
    sha = sha256_bytes(data)
    if plan_path.exists() and not force:
        existing = P.load_plan(plan_path)
        if existing["source"]["sha256"] != sha or existing["source"]["path"] != str(md):
            raise ValueError(f"{plan_path} 已存在且对应不同的源文件；使用 --force 覆盖或换 --work 目录")
        plan, kept = existing, True
    else:
        work.mkdir(parents=True, exist_ok=True)
        plan = P.draft_plan(md, sha, len(lines), blocks, cl, P.suggest_noise(blocks))
        P.save_plan(plan, plan_path)
        kept = False
    (work / "issues.json").write_text(json.dumps([i.to_dict() for i in issues], ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    (work / "issues.md").write_text(issues_markdown(issues), encoding="utf-8")
    (work / "outline.md").write_text(P.outline_markdown(plan, blocks, _summary(issues)), encoding="utf-8")
    return {"source": str(md), "work": str(work), "plan": str(plan_path), "plan_kept": kept,
            "lines": len(lines), "chars": len(text), "blocks": len(blocks), "sections": len(plan["sections"]),
            "demoted": len(plan.get("demoted", [])), "noise_lines": len(plan.get("noise_lines", [])),
            "content_list": str(cl) if cl else None, "pages_matched": matched, "issues": _summary(issues)}


def load(work: str | Path) -> Loaded:
    w = Path(work)
    plan_path = w / "plan.json" if w.is_dir() else w
    plan = P.load_plan(plan_path)
    src = Path(plan["source"]["path"])
    data, text = read_source(src)
    if sha256_bytes(data) != plan["source"]["sha256"]:
        raise ValueError(f"源文件 {src} 在 prepare 之后被修改过（sha256 不一致）；"
                         "源文件必须保持原样——修正请写入 plan 的 fixes，或重新 prepare --force")
    lines = split_lines(text)
    blocks = parse_blocks(lines)
    apply_pages(blocks, plan["source"].get("content_list"))
    return Loaded(plan_path.parent, plan, data, lines, blocks)


def refresh_outline(ld: Loaded) -> Path:
    issues_file = ld.work / "issues.json"
    summary = ""
    if issues_file.exists():
        summary = _summary([Issue(**x) for x in json.loads(issues_file.read_text(encoding="utf-8"))])
    out = ld.work / "outline.md"
    out.write_text(P.outline_markdown(ld.plan, ld.blocks, summary), encoding="utf-8")
    return out
