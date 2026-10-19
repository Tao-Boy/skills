#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""user-status: 记录 Agent 提问与用户回答、评估答案质量、维护用户画像（单个 JSON 状态文件）。

    uv run user_status.py init --name 张三
    uv run user_status.py ask <<'EOF'  {"text": "...", "topics": ["物理/电磁学/高斯定理"], "expected_points": ["..."]}  EOF
    uv run user_status.py answer q0001 <<'EOF'  {"text": "用户原话"}  EOF
    uv run user_status.py evaluate q0001 <<'EOF'  {"verdict": "partially_correct", "scores": {...}, "evidence": ["..."]}  EOF
    uv run user_status.py profile

结构化输入一律通过 stdin（或 --data / --file）传 JSON，避免 shell 转义问题。
状态文件：$USER_STATUS_HOME/<profile>.json（默认 ~/.local/share/user-status/default.json）。
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_BACKUPS = 20
MAX_HISTORY = 200

QUESTION_TYPES = ["concept", "calculation", "proof", "application", "factual", "open", "design",
                  "opinion", "preference", "background", "other"]
NON_GRADED_TYPES = {"opinion", "preference", "background"}
VERDICTS = ["correct", "mostly_correct", "partially_correct", "incorrect", "no_answer", "not_applicable"]
ISSUE_TYPES = ["misconception", "knowledge_gap", "reasoning_error", "calculation_slip", "imprecision",
               "incomplete", "off_topic", "unclear_expression", "other"]
MODES = ["quiz", "interview", "tutoring", "survey", "review", "other"]

DEFAULT_RUBRIC = {
    "version": 1,
    "scale": [0, 4],
    "dimensions": [
        {"key": "correctness", "label": "正确性", "weight": 0.4,
         "anchors": {"0": "错误或答非所问", "1": "主要结论错误，仅有零星正确", "2": "部分正确，有明显错误",
                     "3": "基本正确，有小瑕疵", "4": "完全正确"}},
        {"key": "completeness", "label": "完整性", "weight": 0.2,
         "anchors": {"0": "未覆盖任何期望要点", "2": "覆盖约一半要点", "4": "覆盖全部要点"}},
        {"key": "reasoning", "label": "推理与论证", "weight": 0.2,
         "anchors": {"0": "无推理或推理错误", "2": "有推理但跳步/不严密", "4": "推理清晰严密"}},
        {"key": "clarity", "label": "表达清晰", "weight": 0.1,
         "anchors": {"0": "难以理解", "2": "可理解但组织松散", "4": "条理清楚、简洁"}},
        {"key": "precision", "label": "术语与符号规范", "weight": 0.1,
         "anchors": {"0": "术语/符号混乱", "2": "偶有不规范", "4": "规范准确"}},
    ],
}


class QAError(Exception):
    pass


# ------------------------------------------------------------------ storage

def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def store_dir(args) -> Path:
    d = getattr(args, "store", None) or os.environ.get("USER_STATUS_HOME")
    if d:
        return Path(d).expanduser()
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "user-status"


def profile_path(args) -> Path:
    pid = getattr(args, "profile", None) or os.environ.get("USER_STATUS_PROFILE") or "default"
    if not re.fullmatch(r"[\w\-.]+", pid):
        raise QAError(f"profile 名只能包含字母、数字、下划线、点和连字符: {pid!r}")
    return store_dir(args) / f"{pid}.json"


@contextlib.contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    with open(lock, "a+") as fh:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - non-POSIX
            pass
        yield


def load(path: Path) -> dict:
    if not path.exists():
        raise QAError(f"状态文件不存在: {path}（先运行 init）")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise QAError(f"状态文件不是合法 JSON（{e}）；可从 {path.parent / 'backups'} 恢复") from e
    return migrate(state)


def save(path: Path, state: dict) -> None:
    errs = validate_state(state)
    if errs:
        raise QAError("拒绝写入：状态校验失败\n  " + "\n  ".join(errs[:20]))
    state["updated_at"] = now()
    if path.exists():
        bdir = path.parent / "backups"
        bdir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, bdir / f"{path.stem}.{stamp}.json")
        olds = sorted(bdir.glob(f"{path.stem}.*.json"))
        for old in olds[:-MAX_BACKUPS]:
            old.unlink()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def migrate(state: dict) -> dict:
    v = state.get("schema_version", 0)
    if v > SCHEMA_VERSION:
        raise QAError(f"状态文件版本 {v} 高于脚本支持的 {SCHEMA_VERSION}，请更新脚本")
    # future migrations: if v < 2: ...; state["schema_version"] = 2
    state.setdefault("sessions", [])
    state.setdefault("records", [])
    state.setdefault("rubric", copy.deepcopy(DEFAULT_RUBRIC))
    prof = state.setdefault("profile", {})
    for k, d in (("summary", ""), ("level", ""), ("traits", []), ("misconceptions", []), ("preferences", []),
                 ("goals", []), ("recommendations", []), ("history", [])):
        prof.setdefault(k, d)
    state["schema_version"] = SCHEMA_VERSION
    return state


def new_state(name: str, about: str, profile_id: str) -> dict:
    t = now()
    return migrate({"schema_version": SCHEMA_VERSION, "profile_id": profile_id, "created_at": t, "updated_at": t,
                    "subject": {"name": name, "about": about}, "counters": {"record": 0, "session": 0}})


# ------------------------------------------------------------------ validation

def _num(x, lo, hi) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and lo <= x <= hi


def validate_state(state: dict) -> list[str]:
    errs: list[str] = []
    dims = {d["key"] for d in state.get("rubric", {}).get("dimensions", [])}
    lo, hi = state.get("rubric", {}).get("scale", [0, 4])
    sids = {s["id"] for s in state.get("sessions", [])}
    rids: set[str] = set()
    for r in state.get("records", []):
        rid = r.get("id")
        if not rid or rid in rids:
            errs.append(f"记录 id 缺失或重复: {rid}")
        rids.add(rid)
        q = r.get("question", {})
        if not q.get("text"):
            errs.append(f"{rid}: question.text 为空")
        if q.get("type") not in QUESTION_TYPES:
            errs.append(f"{rid}: question.type 无效 {q.get('type')!r}")
        if q.get("difficulty") is not None and not _num(q["difficulty"], 1, 5):
            errs.append(f"{rid}: difficulty 应为 1-5")
        if r.get("session_id") and r["session_id"] not in sids:
            errs.append(f"{rid}: session_id {r['session_id']} 不存在")
        n_att = len(r.get("answers", []))
        for a in r.get("answers", []):
            if not isinstance(a.get("text"), str):
                errs.append(f"{rid}: answer.text 必须是字符串")
            if a.get("self_confidence") is not None and not _num(a["self_confidence"], 0, 1):
                errs.append(f"{rid}: self_confidence 应为 0-1")
        for ev in r.get("evaluations", []):
            if ev.get("verdict") not in VERDICTS:
                errs.append(f"{rid}: verdict 无效 {ev.get('verdict')!r}")
            if not 1 <= ev.get("attempt", 0) <= max(n_att, 1):
                errs.append(f"{rid}: evaluation.attempt 越界")
            for k, v in (ev.get("scores") or {}).items():
                if k not in dims:
                    errs.append(f"{rid}: 未知评分维度 {k!r}")
                elif not _num(v, lo, hi):
                    errs.append(f"{rid}: 分数 {k}={v} 超出 {lo}-{hi}")
            if ev.get("overall") is not None and not _num(ev["overall"], 0, 100):
                errs.append(f"{rid}: overall 应为 0-100")
            for it in ev.get("issues", []):
                if it.get("type") not in ISSUE_TYPES:
                    errs.append(f"{rid}: issue.type 无效 {it.get('type')!r}")
    for r in state.get("records", []):
        for ref in [r.get("question", {}).get("parent")] + r.get("followups", []):
            if ref and ref not in rids:
                errs.append(f"{r['id']}: 引用了不存在的记录 {ref}")
    prof = state.get("profile", {})
    for coll in ("traits", "misconceptions", "preferences", "goals"):
        for item in prof.get(coll, []):
            for ref in item.get("evidence", []) if isinstance(item, dict) else []:
                if ref not in rids:
                    errs.append(f"profile.{coll}: 证据引用了不存在的记录 {ref}")
    return errs


# ------------------------------------------------------------------ helpers

def read_payload(args, required: bool = True):
    raw = None
    if getattr(args, "data", None):
        raw = args.data
    elif getattr(args, "file", None):
        raw = Path(args.file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    if raw is None or not raw.strip():
        if required:
            raise QAError("需要通过 stdin、--data 或 --file 提供 JSON")
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise QAError(f"输入不是合法 JSON: {e}") from e


def find(state: dict, rid: str) -> dict:
    rid = normalize_id(rid)
    for r in state["records"]:
        if r["id"] == rid:
            return r
    raise QAError(f"记录不存在: {rid}")


def normalize_id(rid: str) -> str:
    rid = rid.strip()
    return f"q{int(rid):04d}" if rid.isdigit() else rid


def open_session(state: dict) -> dict | None:
    for s in reversed(state["sessions"]):
        if not s.get("ended_at"):
            return s
    return None


def norm_topics(topics) -> list[str]:
    if isinstance(topics, str):
        topics = [topics]
    out = []
    for t in topics or []:
        t = "/".join(p.strip() for p in re.split(r"[/>＞›]", str(t)) if p.strip())
        if t:
            out.append(t)
    return list(dict.fromkeys(out))


def latest_eval(r: dict) -> dict | None:
    evs = [e for e in r.get("evaluations", []) if not e.get("superseded")]
    return evs[-1] if evs else None


def compute_overall(state: dict, scores: dict) -> float | None:
    dims = {d["key"]: d.get("weight", 1) for d in state["rubric"]["dimensions"]}
    lo, hi = state["rubric"]["scale"]
    used = {k: v for k, v in scores.items() if k in dims and v is not None}
    if not used:
        return None
    wsum = sum(dims[k] for k in used)
    return round(sum(dims[k] * (v - lo) / (hi - lo) for k, v in used.items()) / wsum * 100, 1)


def check_evidence(answer_text: str, evidence: list[str]) -> list[str]:
    squash = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    a = squash(answer_text)
    return [e for e in evidence if squash(e) not in a]


def emit(args, data, text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        print(text)


def snippet(s: str, n: int = 60) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


# ------------------------------------------------------------------ analytics

LEVELS = [(0.8, "精通"), (0.6, "熟练"), (0.4, "基础"), (0.0, "薄弱")]


def level_of(m: float, n: int) -> str:
    if n < 2:
        return "数据不足"
    return next(label for th, label in LEVELS if m >= th)


def compute_stats(state: dict) -> dict:
    graded = []  # (record, eval, score 0-1)
    verdicts: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    pending_answer, pending_eval = [], []
    for r in state["records"]:
        ev = latest_eval(r)
        if not r.get("answers"):
            pending_answer.append(r["id"])
            continue
        if ev is None:
            pending_eval.append(r["id"])
            continue
        verdicts[ev["verdict"]] = verdicts.get(ev["verdict"], 0) + 1
        for it in ev.get("issues", []):
            issue_counts[it["type"]] = issue_counts.get(it["type"], 0) + 1
        if ev.get("overall") is not None and ev["verdict"] != "not_applicable":
            graded.append((r, ev, ev["overall"] / 100))

    # topic mastery: recency-weighted (0.85^k from newest) x difficulty weight, aggregated on every prefix
    by_topic: dict[str, list] = {}
    for r, ev, s in graded:
        for t in norm_topics(r["question"].get("topics")) or ["(未分类)"]:
            parts = t.split("/")
            for i in range(1, len(parts) + 1):
                by_topic.setdefault("/".join(parts[:i]), []).append((r, s))
    topics = {}
    for t, items in by_topic.items():
        items = sorted(items, key=lambda x: x[0].get("asked_at", ""), reverse=True)
        num = den = 0.0
        for k, (r, s) in enumerate(items):
            w = 0.85 ** k * ((r["question"].get("difficulty") or 3) / 3)
            num += w * s
            den += w
        m = num / den if den else 0.0
        recent = [s for _, s in items[:3]]
        older = [s for _, s in items[3:6]]
        trend = ""
        if recent and older:
            d = sum(recent) / len(recent) - sum(older) / len(older)
            trend = "上升" if d > 0.1 else "下降" if d < -0.1 else "持平"
        topics[t] = {"mastery": round(m, 3), "n": len(items), "confidence": round(len(items) / (len(items) + 3), 2),
                     "level": level_of(m, len(items)), "trend": trend,
                     "last_seen": items[0][0].get("asked_at", "")[:10]}

    dims = {}
    for d in state["rubric"]["dimensions"]:
        vals = [ev["scores"][d["key"]] for _, ev, _ in graded if d["key"] in (ev.get("scores") or {})]
        if vals:
            lo, hi = state["rubric"]["scale"]
            dims[d["key"]] = {"label": d["label"], "mean": round(sum(vals) / len(vals), 2),
                              "pct": round((sum(vals) / len(vals) - lo) / (hi - lo) * 100, 1), "n": len(vals)}

    # misconceptions observed in evaluations (aggregated by concept), merged with curated status
    observed: dict[str, dict] = {}
    for r, ev in ((r, latest_eval(r)) for r in state["records"]):
        if not ev:
            continue
        for it in ev.get("issues", []):
            if it["type"] != "misconception":
                continue
            key = it.get("concept") or it.get("detail", "")[:30]
            o = observed.setdefault(key, {"concept": key, "records": [], "details": []})
            o["records"].append(r["id"])
            if it.get("detail") and it["detail"] not in o["details"]:
                o["details"].append(it["detail"])
    curated = {m["concept"]: m for m in state["profile"].get("misconceptions", [])}
    misconceptions = []
    for key, o in observed.items():
        c = curated.get(key, {})
        misconceptions.append({**o, "status": c.get("status", "active"), "note": c.get("note", "")})

    # calibration: self-reported confidence vs actual score
    pairs = []
    for r, ev, s in graded:
        att = r["answers"][ev["attempt"] - 1]
        if att.get("self_confidence") is not None:
            pairs.append(att["self_confidence"] - s)
    calibration = None
    if len(pairs) >= 3:
        bias = sum(pairs) / len(pairs)
        calibration = {"bias": round(bias, 3), "n": len(pairs),
                       "label": "偏自信" if bias > 0.15 else "偏保守" if bias < -0.15 else "校准良好"}

    scores = [s for _, _, s in sorted(graded, key=lambda x: x[0].get("asked_at", ""))]
    overall = {"n_graded": len(scores), "mean": round(sum(scores) / len(scores) * 100, 1) if scores else None}
    if len(scores) >= 6:
        a, b = scores[-5:], scores[-10:-5]
        overall["recent5"] = round(sum(a) / len(a) * 100, 1)
        overall["prev5"] = round(sum(b) / len(b) * 100, 1)
    hints = [sum(x.get("hints_used", 0) for x in r["answers"]) for r, _, _ in graded]
    return {
        "records": len(state["records"]), "sessions": len(state["sessions"]),
        "verdicts": verdicts, "issues": issue_counts, "overall": overall, "dimensions": dims,
        "topics": dict(sorted(topics.items())), "misconceptions": misconceptions, "calibration": calibration,
        "avg_hints": round(sum(hints) / len(hints), 2) if hints else None,
        "multi_attempt": sum(1 for r, _, _ in graded if len(r["answers"]) > 1),
        "pending_answer": pending_answer, "pending_evaluation": pending_eval,
    }


# ------------------------------------------------------------------ rendering

def render_record(r: dict, full: bool = True) -> str:
    q = r["question"]
    ev = latest_eval(r)
    head = (f"[{r['id']}] {q['type']}"
            + (f" · 难度{q['difficulty']}" if q.get("difficulty") else "")
            + (f" · {', '.join(norm_topics(q.get('topics')))}" if q.get("topics") else "")
            + f" · {r.get('asked_at', '')[:16]}")
    out = [head, f"问：{q['text']}"]
    if full and q.get("expected_points"):
        out.append("期望要点：" + "；".join(q["expected_points"]))
    for i, a in enumerate(r.get("answers", []), 1):
        meta = []
        if a.get("self_confidence") is not None:
            meta.append(f"自评把握 {a['self_confidence']:.0%}")
        if a.get("hints_used"):
            meta.append(f"提示 {a['hints_used']} 次")
        tag = f"（{'，'.join(meta)}）" if meta else ""
        out.append(f"答#{i}{tag}：{a['text'] if full else snippet(a['text'])}")
    if ev:
        sc = ", ".join(f"{k}={v}" for k, v in (ev.get("scores") or {}).items())
        out.append(f"评估（答#{ev['attempt']}）：{ev['verdict']}"
                   + (f" · {ev['overall']}" if ev.get("overall") is not None else "")
                   + (f" · {sc}" if sc else "") + (f" · 评估置信度 {ev['confidence']}" if ev.get("confidence") else ""))
        if full:
            for s in ev.get("strengths", []):
                out.append(f"  + {s}")
            for it in ev.get("issues", []):
                out.append(f"  - [{it['type']}] {it.get('concept', '')}{'：' if it.get('concept') else ''}"
                           f"{it.get('detail', '')}")
            for e in ev.get("evidence", []):
                out.append(f"  > 「{e}」")
            if ev.get("feedback"):
                out.append(f"  反馈：{ev['feedback']}")
    elif r.get("answers"):
        out.append("评估：（待评估）")
    else:
        out.append("回答：（待回答）")
    return "\n".join(out)


def render_profile(state: dict, st: dict) -> str:
    p = state["profile"]
    subj = state.get("subject", {})
    out = [f"# 用户画像：{subj.get('name') or state.get('profile_id')}", ""]
    if subj.get("about"):
        out.append(f"> {subj['about']}\n")
    ov = st["overall"]
    out.append(f"- 记录 {st['records']} 条（已评分 {ov['n_graded']}），会话 {st['sessions']} 次；"
               f"平均得分 {ov['mean'] if ov['mean'] is not None else '—'}"
               + (f"；近5题 {ov['recent5']}（前5题 {ov['prev5']}）" if "recent5" in ov else ""))
    if st["verdicts"]:
        out.append("- 判定分布：" + "，".join(f"{k} {v}" for k, v in st["verdicts"].items()))
    if st["calibration"]:
        c = st["calibration"]
        out.append(f"- 自信校准：{c['label']}（偏差 {c['bias']:+.2f}，n={c['n']}）")
    if st["pending_answer"] or st["pending_evaluation"]:
        out.append(f"- 待处理：未回答 {st['pending_answer']}，未评估 {st['pending_evaluation']}")
    out += ["", "## 总体评价（Agent）", "", p.get("summary") or "（尚未撰写）"]
    if p.get("level"):
        out.append(f"\n总体水平：{p['level']}")
    if st["dimensions"]:
        out += ["", "## 能力维度", "", "| 维度 | 平均分 | 百分制 | n |", "|---|---|---|---|"]
        out += [f"| {d['label']} | {d['mean']} | {d['pct']} | {d['n']} |" for d in st["dimensions"].values()]
    if st["topics"]:
        out += ["", "## 知识掌握（近期加权）", "",
                "| 主题 | 掌握度 | 水平 | n | 趋势 | 最近 |", "|---|---|---|---|---|---|"]
        for t, v in st["topics"].items():
            depth = t.count("/")
            name = ("　" * depth) + t.split("/")[-1]
            out.append(f"| {name} | {v['mastery']:.2f} | {v['level']} | {v['n']} | {v['trend']} | {v['last_seen']} |")
    if st["misconceptions"]:
        out += ["", "## 误区", ""]
        for m in st["misconceptions"]:
            out.append(f"- [{m['status']}] {m['concept']}：{'；'.join(m['details'][:2])}（{', '.join(m['records'])}）"
                       + (f" — {m['note']}" if m.get("note") else ""))
    for key, title in (("traits", "特征（Agent 判断）"), ("preferences", "偏好"), ("goals", "目标"),
                       ("recommendations", "建议")):
        items = p.get(key) or []
        if items:
            out += ["", f"## {title}", ""]
            for it in items:
                if isinstance(it, dict):
                    text = it.get("text") or it.get("trait") or json.dumps(it, ensure_ascii=False)
                    ev = f"（证据 {', '.join(it['evidence'])}）" if it.get("evidence") else ""
                    conf = f" [置信度 {it['confidence']}]" if it.get("confidence") is not None else ""
                    out.append(f"- {text}{conf}{ev}")
                else:
                    out.append(f"- {it}")
    if st["issues"]:
        out += ["", "问题类型统计：" + "，".join(f"{k} {v}" for k, v in sorted(st["issues"].items(),
                                                                         key=lambda x: -x[1]))]
    if p.get("history"):
        h = p["history"][-1]
        out.append(f"\n上次画像更新：{h['at'][:16]}（基于 {h['records']} 条记录）")
    return "\n".join(out)


# ------------------------------------------------------------------ commands

def cmd_init(args):
    path = profile_path(args)
    with locked(path):
        if path.exists():
            st = load(path)
            print(f"已存在：{path}（{len(st['records'])} 条记录）")
            return
        save(path, new_state(args.name or "", args.about or "", path.stem))
    print(f"已创建：{path}")


def cmd_session(args):
    path = profile_path(args)
    with locked(path):
        state = load(path)
        if args.action == "start":
            cur = open_session(state)
            if cur:
                cur["ended_at"] = now()
            state["counters"]["session"] += 1
            sid = f"s{state['counters']['session']:03d}"
            if args.mode not in MODES:
                raise QAError(f"mode 必须是 {MODES}")
            state["sessions"].append({"id": sid, "started_at": now(), "ended_at": None, "mode": args.mode,
                                      "topic": args.topic or "", "goal": args.goal or "", "summary": ""})
            save(path, state)
            print(f"会话开始：{sid}" + (f"（已结束上一会话 {cur['id']}）" if cur else ""))
        else:
            cur = open_session(state)
            if not cur:
                raise QAError("没有进行中的会话")
            cur["ended_at"] = now()
            if args.summary:
                cur["summary"] = args.summary
            save(path, state)
            n = sum(1 for r in state["records"] if r.get("session_id") == cur["id"])
            print(f"会话结束：{cur['id']}（{n} 条记录）")


def _build_question(data: dict) -> dict:
    if not isinstance(data, dict) or not str(data.get("text", "")).strip():
        raise QAError("question 需要非空 text")
    qtype = data.get("type", "concept")
    if qtype not in QUESTION_TYPES:
        raise QAError(f"question.type 必须是 {QUESTION_TYPES}")
    allowed = {"text", "type", "topics", "difficulty", "intent", "expected_points", "reference_answer",
               "source", "parent", "tags", "context"}
    extra = set(data) - allowed
    if extra:
        raise QAError(f"question 有未知字段 {sorted(extra)}；可用字段 {sorted(allowed)}")
    q = {k: data[k] for k in allowed if k in data}
    q["type"] = qtype
    q["topics"] = norm_topics(data.get("topics"))
    q.setdefault("expected_points", [])
    return q


def _add_record(state: dict, q: dict) -> dict:
    if q.get("parent"):
        q["parent"] = normalize_id(q["parent"])
        parent = find(state, q["parent"])
    state["counters"]["record"] += 1
    rid = f"q{state['counters']['record']:04d}"
    sess = open_session(state)
    rec = {"id": rid, "session_id": sess["id"] if sess else None, "asked_at": now(), "question": q,
           "answers": [], "evaluations": [], "followups": []}
    state["records"].append(rec)
    if q.get("parent"):
        parent["followups"].append(rid)
    return rec


def _build_answer(data) -> dict:
    if isinstance(data, str):
        data = {"text": data}
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise QAError("answer 需要 text（用户原话，逐字记录）")
    allowed = {"text", "self_confidence", "hints_used", "latency_s", "notes", "format"}
    extra = set(data) - allowed
    if extra:
        raise QAError(f"answer 有未知字段 {sorted(extra)}；可用字段 {sorted(allowed)}")
    a = {k: data[k] for k in allowed if k in data}
    a["answered_at"] = now()
    a.setdefault("hints_used", 0)
    return a


def _build_eval(state: dict, rec: dict, data: dict) -> tuple[dict, list[str]]:
    if not rec["answers"]:
        raise QAError(f"{rec['id']} 还没有回答，不能评估")
    allowed = {"attempt", "verdict", "scores", "overall", "strengths", "issues", "evidence", "confidence",
               "feedback", "notes", "reason"}
    extra = set(data) - allowed
    if extra:
        raise QAError(f"evaluation 有未知字段 {sorted(extra)}；可用字段 {sorted(allowed)}")
    att = int(data.get("attempt") or len(rec["answers"]))
    if not 1 <= att <= len(rec["answers"]):
        raise QAError(f"attempt 越界（共 {len(rec['answers'])} 次回答）")
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        raise QAError(f"verdict 必须是 {VERDICTS}")
    scores = data.get("scores") or {}
    graded = rec["question"]["type"] not in NON_GRADED_TYPES and verdict not in ("not_applicable",)
    if graded and verdict != "no_answer" and not scores:
        raise QAError("可评分的问题需要 scores（按 rubric 维度 0-4 打分）；观点/偏好类请用 verdict=not_applicable")
    issues = data.get("issues") or []
    for it in issues:
        if not isinstance(it, dict) or it.get("type") not in ISSUE_TYPES:
            raise QAError(f"issues 每项需要 type ∈ {ISSUE_TYPES}")
    ev = {"attempt": att, "verdict": verdict, "scores": scores,
          "overall": data.get("overall") if data.get("overall") is not None else compute_overall(state, scores),
          "strengths": data.get("strengths") or [], "issues": issues, "evidence": data.get("evidence") or [],
          "confidence": data.get("confidence"), "feedback": data.get("feedback", ""), "notes": data.get("notes", ""),
          "rubric_version": state["rubric"]["version"], "evaluated_at": now()}
    if verdict == "no_answer" and ev["overall"] is None:
        ev["overall"] = 0.0
    warnings = []
    bad = check_evidence(rec["answers"][att - 1]["text"], ev["evidence"])
    if bad:
        warnings.append("以下 evidence 不是回答原文的片段（应逐字引用）：" + "；".join(bad))
    c = scores.get("correctness")
    if c is not None and ((verdict == "correct" and c < 3) or (verdict == "incorrect" and c >= 3)):
        warnings.append(f"verdict={verdict} 与 correctness={c} 不一致，请核对")
    if graded and not ev["evidence"] and verdict not in ("no_answer",):
        warnings.append("建议在 evidence 中引用回答原文作为评判依据")
    if ev["confidence"] is not None and not _num(ev["confidence"], 0, 1):
        raise QAError("confidence 应为 0-1")
    return ev, warnings


def cmd_ask(args):
    path = profile_path(args)
    data = read_payload(args)
    with locked(path):
        state = load(path)
        rec = _add_record(state, _build_question(data))
        save(path, state)
    emit(args, rec, f"已记录问题 {rec['id']}" + (f"（会话 {rec['session_id']}）" if rec["session_id"] else ""))


def cmd_answer(args):
    path = profile_path(args)
    data = {"text": args.text} if args.text is not None else read_payload(args)
    with locked(path):
        state = load(path)
        rec = find(state, args.id)
        rec["answers"].append(_build_answer(data))
        save(path, state)
    n = len(rec["answers"])
    print(f"已记录 {rec['id']} 的第 {n} 次回答（{len(rec['answers'][-1]['text'])} 字）")


def cmd_evaluate(args, revise: bool = False):
    path = profile_path(args)
    data = read_payload(args)
    with locked(path):
        state = load(path)
        rec = find(state, args.id)
        ev, warnings = _build_eval(state, rec, data)
        prev = [e for e in rec["evaluations"] if e["attempt"] == ev["attempt"] and not e.get("superseded")]
        if prev and not revise:
            raise QAError(f"{rec['id']} 第 {ev['attempt']} 次回答已评估；如需修改请用 revise 并说明 reason")
        if revise:
            if not data.get("reason"):
                raise QAError("revise 需要 reason（为什么修改评估）")
            for e in prev:
                e["superseded"] = True
            ev["revision_reason"] = data["reason"]
        rec["evaluations"].append(ev)
        save(path, state)
    msg = f"已{'修订' if revise else '记录'} {rec['id']} 的评估：{ev['verdict']}" + \
        (f" · {ev['overall']}" if ev["overall"] is not None else "")
    emit(args, {"evaluation": ev, "warnings": warnings}, "\n".join([msg] + [f"! {w}" for w in warnings]))


def cmd_log(args):
    path = profile_path(args)
    data = read_payload(args)
    items = data if isinstance(data, list) else [data]
    out, warns = [], []
    with locked(path):
        state = load(path)
        for item in items:
            rec = _add_record(state, _build_question(item.get("question") or {}))
            for a in item.get("answers") or ([item["answer"]] if item.get("answer") is not None else []):
                rec["answers"].append(_build_answer(a))
            if item.get("evaluation"):
                ev, w = _build_eval(state, rec, item["evaluation"])
                rec["evaluations"].append(ev)
                warns += [f"{rec['id']}: {x}" for x in w]
            out.append(rec["id"])
        save(path, state)
    emit(args, {"ids": out, "warnings": warns}, "\n".join([f"已记录 {', '.join(out)}"] + [f"! {w}" for w in warns]))


def cmd_list(args):
    state = load(profile_path(args))
    recs = state["records"]
    if args.session:
        recs = [r for r in recs if r.get("session_id") == args.session]
    if args.topic:
        recs = [r for r in recs if any(t.startswith(args.topic) or args.topic in t
                                       for t in norm_topics(r["question"].get("topics")))]
    if args.verdict:
        recs = [r for r in recs if (latest_eval(r) or {}).get("verdict") in args.verdict]
    if args.issue:
        recs = [r for r in recs if any(i["type"] == args.issue for i in (latest_eval(r) or {}).get("issues", []))]
    if args.since:
        recs = [r for r in recs if r.get("asked_at", "") >= args.since]
    if args.pending:
        recs = [r for r in recs if not r["answers"] or not latest_eval(r)]
    recs = recs[-args.limit:]
    lines = []
    for r in recs:
        ev = latest_eval(r)
        status = (f"{ev['verdict']}{' ' + str(ev['overall']) if ev.get('overall') is not None else ''}" if ev
                  else "待评估" if r["answers"] else "待回答")
        lines.append(f"{r['id']} {r.get('asked_at', '')[:10]} [{status}] "
                     f"{','.join(norm_topics(r['question'].get('topics')))} | {snippet(r['question']['text'], 50)}")
    emit(args, recs, "\n".join(lines) or "（无记录）")


def cmd_show(args):
    state = load(profile_path(args))
    recs = [find(state, i) for i in args.id]
    emit(args, recs, "\n\n".join(render_record(r) for r in recs))


def cmd_profile(args):
    path = profile_path(args)
    state = load(path)
    st = compute_stats(state)
    emit(args, {"profile": state["profile"], "stats": st, "subject": state.get("subject")},
         render_profile(state, st))


def cmd_profile_update(args):
    path = profile_path(args)
    data = read_payload(args)
    allowed = {"summary", "level", "traits", "preferences", "goals", "recommendations", "misconceptions", "subject"}
    extra = set(data) - allowed
    if extra:
        raise QAError(f"未知字段 {sorted(extra)}；可用字段 {sorted(allowed)}")
    with locked(path):
        state = load(path)
        prof = state["profile"]
        for k in ("summary", "level"):
            if k in data:
                prof[k] = str(data[k])
        for k in ("traits", "preferences", "goals", "recommendations"):
            if k in data:
                if not isinstance(data[k], list):
                    raise QAError(f"{k} 必须是列表（整体替换）")
                prof[k] = [x if isinstance(x, dict) else {"text": str(x)} for x in data[k]]
                for x in prof[k]:
                    x["evidence"] = [normalize_id(e) for e in x.get("evidence", [])]
        for m in data.get("misconceptions", []):
            if not m.get("concept") or m.get("status") not in ("active", "resolved", "dismissed"):
                raise QAError("misconceptions 每项需要 concept 和 status ∈ active/resolved/dismissed")
            m = {**m, "evidence": [normalize_id(e) for e in m.get("evidence", [])], "updated_at": now()}
            cur = next((x for x in prof["misconceptions"] if x["concept"] == m["concept"]), None)
            if cur:
                cur.update(m)
            else:
                prof["misconceptions"].append(m)
        if "subject" in data:
            state["subject"].update({k: v for k, v in data["subject"].items() if k in ("name", "about")})
        st = compute_stats(state)
        prof["history"].append({"at": now(), "records": st["records"], "graded": st["overall"]["n_graded"],
                                "mean": st["overall"]["mean"], "level": prof.get("level", ""),
                                "summary": prof.get("summary", ""),
                                "topics": {t: v["mastery"] for t, v in st["topics"].items() if "/" not in t}})
        prof["history"] = prof["history"][-MAX_HISTORY:]
        prof["updated_at"] = now()
        save(path, state)
    print(f"画像已更新（第 {len(prof['history'])} 版快照）")


def cmd_report(args):
    state = load(profile_path(args))
    st = compute_stats(state)
    parts = [render_profile(state, st)]
    hist = state["profile"].get("history", [])
    if len(hist) > 1:
        parts += ["", "## 画像演变", "", "| 时间 | 记录数 | 平均分 | 水平 |", "|---|---|---|---|"]
        parts += [f"| {h['at'][:16]} | {h['records']} | {h['mean']} | {h.get('level', '')} |" for h in hist[-20:]]
    recs = state["records"][-args.recent:] if args.recent else state["records"]
    parts += ["", f"## 问答记录（{len(recs)} 条）", ""]
    parts += [render_record(r) + "\n" for r in recs]
    text = "\n".join(parts)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"已写入 {args.output}")
    else:
        print(text)


def cmd_validate(args):
    state = load(profile_path(args))
    errs = validate_state(state)
    print("\n".join(errs) if errs else f"✓ 状态有效（{len(state['records'])} 条记录，schema v{SCHEMA_VERSION}）")
    return 1 if errs else 0


def cmd_delete(args):
    path = profile_path(args)
    with locked(path):
        state = load(path)
        ids = {normalize_id(i) for i in args.id}
        for i in ids:
            find(state, i)
        state["records"] = [r for r in state["records"] if r["id"] not in ids]
        for r in state["records"]:
            r["followups"] = [x for x in r["followups"] if x not in ids]
            if r["question"].get("parent") in ids:
                r["question"]["parent"] = None
        for coll in ("traits", "preferences", "goals", "recommendations", "misconceptions"):
            for it in state["profile"].get(coll, []):
                if isinstance(it, dict):
                    it["evidence"] = [e for e in it.get("evidence", []) if e not in ids]
        save(path, state)
    print(f"已删除 {', '.join(sorted(ids))}（删除前的状态保存在 backups/）")


def cmd_rubric(args):
    path = profile_path(args)
    if args.action == "show":
        state = load(path)
        r = state["rubric"]
        lines = [f"rubric v{r['version']}，分值 {r['scale'][0]}-{r['scale'][1]}"]
        for d in r["dimensions"]:
            lines.append(f"- {d['key']}（{d['label']}，权重 {d['weight']}）")
            lines += [f"    {k}: {v}" for k, v in d.get("anchors", {}).items()]
        emit(args, r, "\n".join(lines))
        return
    data = read_payload(args)
    dims = data.get("dimensions") if isinstance(data, dict) else None
    if not dims or not all(isinstance(d, dict) and d.get("key") and d.get("label") for d in dims):
        raise QAError("rubric 需要 dimensions: [{key, label, weight, anchors}]")
    with locked(path):
        state = load(path)
        used = {k for r in state["records"] for e in r["evaluations"] for k in (e.get("scores") or {})}
        missing = used - {d["key"] for d in dims}
        if missing:
            raise QAError(f"已有评估使用了维度 {sorted(missing)}，新 rubric 必须保留它们")
        state["rubric"] = {"version": state["rubric"]["version"] + 1,
                           "scale": data.get("scale", state["rubric"]["scale"]),
                           "dimensions": [{"weight": 1, **d} for d in dims]}
        save(path, state)
    print(f"rubric 已更新为 v{state['rubric']['version']}")


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", "-p", help="画像名（默认 $USER_STATUS_PROFILE 或 default）")
    common.add_argument("--store", help="存储目录（默认 $USER_STATUS_HOME 或 ~/.local/share/user-status）")
    common.add_argument("--json", action="store_true", help="输出 JSON")
    payload = argparse.ArgumentParser(add_help=False)
    payload.add_argument("--data", "-d", help="JSON 字符串（默认从 stdin 读取）")
    payload.add_argument("--file", "-f", help="从文件读取 JSON")

    p = argparse.ArgumentParser(prog="user_status.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, with_payload=False):
        sp = sub.add_parser(name, parents=[common] + ([payload] if with_payload else []), help=help_,
                            description=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, "创建画像状态文件")
    sp.add_argument("--name", default="")
    sp.add_argument("--about", default="", help="用户自述的背景")

    sp = add("session", cmd_session, "开始/结束一次问答会话")
    sp.add_argument("action", choices=["start", "end"])
    sp.add_argument("--topic")
    sp.add_argument("--goal")
    sp.add_argument("--mode", default="quiz", help=f"{'/'.join(MODES)}")
    sp.add_argument("--summary", help="结束时的会话小结")

    add("ask", cmd_ask, "记录 Agent 提出的问题（在用户回答前写好期望要点）", True)

    sp = add("answer", cmd_answer, "逐字记录用户回答（重复调用 = 再次作答）", True)
    sp.add_argument("id")
    sp.add_argument("--text", help="直接给出回答原文（等价于 {\"text\": ...}）")

    sp = add("evaluate", cmd_evaluate, "记录对回答的评估", True)
    sp.add_argument("id")
    sp = add("revise", lambda a: cmd_evaluate(a, revise=True), "修订已有评估（保留旧版本，需 reason）", True)
    sp.add_argument("id")

    add("log", cmd_log, "一次性记录 问题+回答+评估（可为列表）", True)

    sp = add("list", cmd_list, "列出记录")
    sp.add_argument("--topic")
    sp.add_argument("--session")
    sp.add_argument("--verdict", action="append", choices=VERDICTS)
    sp.add_argument("--issue", choices=ISSUE_TYPES)
    sp.add_argument("--since", help="ISO 日期，如 2026-10-01")
    sp.add_argument("--pending", action="store_true", help="只看待回答/待评估")
    sp.add_argument("--limit", type=int, default=50)

    sp = add("show", cmd_show, "查看记录详情")
    sp.add_argument("id", nargs="+")

    add("profile", cmd_profile, "查看画像（统计自动计算 + Agent 判断）")
    add("profile-update", cmd_profile_update, "更新 Agent 撰写的画像字段并保存快照", True)

    sp = add("report", cmd_report, "生成 Markdown 报告")
    sp.add_argument("--output", "-o")
    sp.add_argument("--recent", type=int, default=0, help="只附最近 N 条记录（0=全部）")

    add("validate", cmd_validate, "校验状态文件")

    sp = add("delete", cmd_delete, "删除记录（用户要求遗忘时）")
    sp.add_argument("id", nargs="+")

    sp = add("rubric", cmd_rubric, "查看或替换评分标准", True)
    sp.add_argument("action", choices=["show", "set"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except QAError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
