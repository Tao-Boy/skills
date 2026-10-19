#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""shadow-users: 基于用户画像创建「影子用户」，从不同视角/场景/时间模拟用户对问题的思考。

    uv run shadows.py init
    uv run shadows.py core set   <<'EOF'  {...共同内核...}  EOF
    uv run shadows.py create     <<'EOF'  [{...影子...}, ...]  EOF
    uv run shadows.py run start  <<'EOF'  {"problem": "..."}  EOF      # 输出每个影子的模拟简报
    uv run shadows.py run add r001 sh01 <<'EOF'  {...影子的思考...}  EOF
    uv run shadows.py run synthesize r001 <<'EOF'  {...综合...}  EOF
    uv run shadows.py run compare r001 <<'EOF'  {"actual": "用户真实回答", "fidelity": {...}}  EOF

结构化输入一律通过 stdin（或 --data / --file）传 JSON。
状态文件：$SHADOW_USERS_HOME/<profile>.json（默认 ~/.local/share/shadow-users/default.json）。
"""

from __future__ import annotations

import argparse
import contextlib
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
LOW_FIDELITY = 0.5

COGNITION_KEYS = {
    "abstraction": "抽象能力",
    "rigor": "严谨性",
    "working_depth": "可稳定处理的推理深度",
    "speed": "反应速度",
    "metacognition": "元认知（知道自己不知道）",
}
# capability dimensions a shadow may not change while it lives in the present
CAPABILITY_KEYS = {"abstraction", "rigor", "working_depth", "metacognition", "intelligence", "knowledge", "智力",
                   "认知水平", "知识水平", "抽象能力", "严谨性", "推理深度", "元认知"}
TIME_MODES = ["past", "present", "future"]

LENSES = {
    "first_principles": ("第一性原理", "从定义和最基本的规律出发，一步步推", ["它的定义是什么？", "最基本的规律是哪条？"]),
    "pragmatist": ("实用主义", "关心怎么用、最快怎么解决、有什么用", ["最快的解法是什么？", "这在实际中有什么用？"]),
    "skeptic": ("怀疑者", "找前提、漏洞和反例，不轻易接受结论", ["前提成立吗？", "有没有反例？"]),
    "intuitive": ("直觉/图像", "用图像、物理直观、几何直观来理解", ["画出来是什么样？", "直觉上应该怎样？"]),
    "analogist": ("类比者", "把新问题和已经熟悉的东西联系起来", ["这像我学过的什么？", "类比在哪里失效？"]),
    "detail": ("细节控", "关注条件、边界、符号、单位", ["适用条件是什么？", "边界/特殊情况呢？"]),
    "big_picture": ("全局观", "看它在知识体系或整件事中的位置与联系", ["它和哪些东西相关？", "为什么重要？"]),
    "teacher": ("讲解者", "设想要把它讲给别人听", ["怎么讲最清楚？", "别人会在哪里卡住？"]),
    "confused_self": ("困惑的自己", "暴露最可能卡住、误解的地方", ["我哪里没懂？", "哪一步最容易错？"]),
    "risk": ("风险视角", "想可能出错的地方和代价", ["最坏会怎样？", "怎么验证没错？"]),
    "creative": ("发散者", "推广、变式、提出新问题", ["换个条件会怎样？", "能推广吗？"]),
    "devils_advocate": ("唱反调", "刻意站在主流结论的反面", ["如果结论是错的，理由会是什么？"]),
    "stakeholder": ("他人视角", "站在老师/同学/面试官/客户等他人的位置想", ["对方关心什么？", "对方会怎么评价？"]),
    "emotional": ("感受与动机", "关注兴趣、焦虑、动力（适合决策/学习规划类问题）", ["我对此感觉如何？", "什么在驱动我？"]),
}
SCENARIOS = {
    "exam": "限时考试，紧张，没有资料",
    "tired": "深夜疲惫，注意力下降",
    "discussion": "与同学讨论，被追问",
    "explaining": "给别人讲解",
    "project": "实际项目或应用中遇到",
    "interview": "面试中被问到",
    "relaxed": "放松地自由探索，时间充裕",
}


class ShadowError(Exception):
    pass


# ------------------------------------------------------------------ storage

def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def state_path(args) -> Path:
    d = getattr(args, "store", None) or os.environ.get("SHADOW_USERS_HOME")
    base = Path(d).expanduser() if d else \
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "shadow-users"
    pid = getattr(args, "profile", None) or os.environ.get("SHADOW_USERS_PROFILE") or "default"
    if not re.fullmatch(r"[\w\-.]+", pid):
        raise ShadowError(f"profile 名只能包含字母、数字、下划线、点和连字符: {pid!r}")
    return base / f"{pid}.json"


@contextlib.contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+") as fh:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover
            pass
        yield


def load(path: Path) -> dict:
    if not path.exists():
        raise ShadowError(f"状态文件不存在: {path}（先运行 init）")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ShadowError(f"状态文件不是合法 JSON（{e}）；可从 {path.parent / 'backups'} 恢复") from e
    return migrate(state)


def migrate(state: dict) -> dict:
    v = state.get("schema_version", 0)
    if v > SCHEMA_VERSION:
        raise ShadowError(f"状态文件版本 {v} 高于脚本支持的 {SCHEMA_VERSION}，请更新脚本")
    state.setdefault("core", None)
    state.setdefault("core_history", [])
    state.setdefault("shadows", [])
    state.setdefault("runs", [])
    state.setdefault("counters", {"shadow": 0, "run": 0})
    state["schema_version"] = SCHEMA_VERSION
    return state


def save(path: Path, state: dict) -> None:
    errs = validate_state(state)
    if errs:
        raise ShadowError("拒绝写入：状态校验失败\n  " + "\n  ".join(errs[:20]))
    state["updated_at"] = now()
    if path.exists():
        bdir = path.parent / "backups"
        bdir.mkdir(exist_ok=True)
        shutil.copy2(path, bdir / f"{path.stem}.{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json")
        for old in sorted(bdir.glob(f"{path.stem}.*.json"))[:-MAX_BACKUPS]:
            old.unlink()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ------------------------------------------------------------------ validation

def _rating(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 1 <= x <= 5


def check_core(core: dict) -> list[str]:
    errs = []
    if not isinstance(core, dict):
        return ["core 必须是对象"]
    allowed = {"name", "source", "summary", "cognition", "knowledge", "misconceptions", "thinking_style",
               "expression", "values", "goals", "context", "data_confidence", "gaps", "updated_at"}
    extra = set(core) - allowed
    if extra:
        errs.append(f"core 有未知字段 {sorted(extra)}；可用字段 {sorted(allowed)}")
    if not core.get("source"):
        errs.append("core.source 必填：说明画像来自哪里（用户自述/对话记录/画像文件…）")
    cog = core.get("cognition") or {}
    for k, v in cog.items():
        if k == "notes":
            continue
        val = v.get("level") if isinstance(v, dict) else v
        if not _rating(val):
            errs.append(f"core.cognition.{k} 需要 1-5 的评级（或 {{level, evidence}}）")
    for i, k in enumerate(core.get("knowledge") or []):
        if not isinstance(k, dict) or not k.get("domain") or not k.get("level"):
            errs.append(f"core.knowledge[{i}] 需要 domain 和 level")
    for i, m in enumerate(core.get("misconceptions") or []):
        if not isinstance(m, dict) or not m.get("concept"):
            errs.append(f"core.misconceptions[{i}] 需要 concept")
    dc = core.get("data_confidence")
    if dc is not None and not (isinstance(dc, (int, float)) and 0 <= dc <= 1):
        errs.append("core.data_confidence 应为 0-1")
    return errs


def check_shadow(sh: dict) -> list[str]:
    errs = []
    allowed = {"id", "name", "lens", "scenario", "time", "adjustments", "knowledge_overrides", "voice", "purpose",
               "status", "created_at", "updated_at", "notes"}
    extra = set(sh) - allowed
    if extra:
        errs.append(f"影子有未知字段 {sorted(extra)}；可用字段 {sorted(allowed)}")
    lens = sh.get("lens")
    if not isinstance(lens, dict) or not (lens.get("key") or lens.get("focus")):
        errs.append("lens 需要 key（内置视角）或 focus（自定义视角描述）")
    elif lens.get("key") and lens["key"] not in LENSES and not lens.get("focus"):
        errs.append(f"未知内置视角 {lens['key']!r}；自定义视角请提供 focus")
    t = sh.get("time") or {"mode": "present"}
    if t.get("mode") not in TIME_MODES:
        errs.append(f"time.mode 必须是 {TIME_MODES}")
    if t.get("mode") in ("past", "future") and not t.get("offset"):
        errs.append("过去/未来的影子需要 time.offset（如 \"-3个月\" / \"+1年\"）")
    if t.get("mode") == "future" and not t.get("assumptions"):
        errs.append("未来的影子需要 time.assumptions（假设了哪些成长）")
    for a in sh.get("adjustments") or []:
        if not isinstance(a, dict) or not a.get("trait") or a.get("direction") not in ("+", "-"):
            errs.append("adjustments 每项需要 trait 和 direction（+ 或 -）")
            continue
        if a["trait"] in CAPABILITY_KEYS and t.get("mode", "present") == "present":
            errs.append(f"当下的影子不能调整能力维度 {a['trait']!r}（影子的智力/认知水平必须与用户一致）；"
                        "只能调整关注点、风格、情绪等，或改用 past/future 时间点")
        elif abs(a.get("magnitude", 1)) > 2:
            errs.append(f"adjustment {a['trait']!r} 幅度过大（magnitude ≤ 2）")
    if sh.get("knowledge_overrides") and t.get("mode", "present") == "present":
        errs.append("knowledge_overrides 只用于 past/future 影子（当下的影子知识边界与用户一致）")
    return errs


def validate_state(state: dict) -> list[str]:
    errs = []
    if state.get("core") is not None:
        errs += check_core(state["core"])
    ids = set()
    for sh in state["shadows"]:
        if sh.get("id") in ids:
            errs.append(f"影子 id 重复 {sh.get('id')}")
        ids.add(sh.get("id"))
        errs += [f"{sh.get('id')}: {e}" for e in check_shadow(sh)]
    for r in state["runs"]:
        for sid in list(r.get("outputs", {})) + list((r.get("comparison") or {}).get("fidelity", {})):
            if sid not in ids:
                errs.append(f"{r['id']}: 引用了不存在的影子 {sid}")
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
            raise ShadowError("需要通过 stdin、--data 或 --file 提供 JSON")
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ShadowError(f"输入不是合法 JSON: {e}") from e


def emit(args, data, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=1) if getattr(args, "json", False) else text)


def get_shadow(state: dict, sid: str) -> dict:
    sid = sid.strip()
    if sid.isdigit():
        sid = f"sh{int(sid):02d}"
    for sh in state["shadows"]:
        if sh["id"] == sid or sh.get("name") == sid:
            return sh
    raise ShadowError(f"影子不存在: {sid}")


def get_run(state: dict, rid: str) -> dict:
    rid = rid.strip()
    if rid.isdigit():
        rid = f"r{int(rid):03d}"
    for r in state["runs"]:
        if r["id"] == rid:
            return r
    raise ShadowError(f"模拟记录不存在: {rid}")


def active_shadows(state: dict) -> list[dict]:
    return [s for s in state["shadows"] if s.get("status", "active") == "active"]


def lens_text(lens: dict) -> tuple[str, str, list[str]]:
    key = lens.get("key")
    label, focus, qs = LENSES.get(key, (key or "自定义", "", []))
    return lens.get("label") or label, lens.get("focus") or focus, lens.get("questions") or qs


def fidelity_stats(state: dict, sid: str) -> dict:
    vals = [r["comparison"]["fidelity"][sid]["score"] for r in state["runs"]
            if r.get("comparison") and sid in r["comparison"].get("fidelity", {})]
    if not vals:
        return {"n": 0, "mean": None}
    return {"n": len(vals), "mean": round(sum(vals) / len(vals), 2)}


def _lvl(v) -> str:
    return str(v.get("level")) if isinstance(v, dict) else str(v)


# ------------------------------------------------------------------ rendering

def render_core(core: dict) -> str:
    out = [f"画像来源：{core.get('source', '')}"
           + (f"（数据置信度 {core['data_confidence']}）" if core.get("data_confidence") is not None else "")]
    if core.get("summary"):
        out.append(f"概述：{core['summary']}")
    cog = core.get("cognition") or {}
    if cog:
        out.append("认知水平（1-5，所有影子相同）：" + "；".join(
            f"{COGNITION_KEYS.get(k, k)} {_lvl(v)}" for k, v in cog.items() if k != "notes"))
        if cog.get("notes"):
            out.append(f"  备注：{cog['notes']}")
    for k in core.get("knowledge") or []:
        line = f"- 知识 {k['domain']}：{k['level']}"
        if k.get("known"):
            line += f"；会：{'、'.join(k['known'])}"
        if k.get("unknown"):
            line += f"；不会/没学过：{'、'.join(k['unknown'])}"
        out.append(line)
    for m in core.get("misconceptions") or []:
        out.append(f"- 误区（{m.get('status', 'active')}）{m['concept']}：{m.get('detail', '')}")
    for key, label in (("thinking_style", "思维习惯"), ("values", "价值与偏好"), ("goals", "目标")):
        if core.get(key):
            out.append(f"{label}：" + "；".join(map(str, core[key])))
    exp = core.get("expression")
    if exp:
        out.append("表达风格：" + ("；".join(f"{k}={v}" for k, v in exp.items()) if isinstance(exp, dict) else str(exp)))
    if core.get("context"):
        out.append(f"背景：{core['context']}")
    if core.get("gaps"):
        out.append("数据缺口（模拟时这些方面要保守、标注不确定）：" + "；".join(core["gaps"]))
    return "\n".join(out)


def render_shadow(sh: dict, state: dict | None = None) -> str:
    label, focus, qs = lens_text(sh["lens"])
    t = sh.get("time") or {"mode": "present"}
    sc = sh.get("scenario") or {}
    out = [f"[{sh['id']}] {sh.get('name', label)} · 视角：{label}" + (f" · {sh.get('status')}" if
                                                                   sh.get("status") == "retired" else "")]
    if focus:
        out.append(f"  关注：{focus}")
    if sc:
        sc_text = SCENARIOS.get(sc.get("key", ""), "")
        out.append(f"  场景：{sc.get('setting') or sc_text}" + (f"；状态：{sc['state']}" if sc.get("state") else "")
                   + (f"；限制：{'、'.join(sc['constraints'])}" if sc.get("constraints") else ""))
    if t.get("mode") != "present":
        out.append(f"  时间：{t['mode']} {t.get('offset', '')}" +
                   (f"；假设：{'；'.join(t['assumptions'])}" if t.get("assumptions") else ""))
    for a in sh.get("adjustments") or []:
        out.append(f"  调整：{a['trait']} {a['direction']}{a.get('magnitude', 1)}" +
                   (f"（{a['note']}）" if a.get("note") else ""))
    for k in sh.get("knowledge_overrides") or []:
        out.append(f"  知识变化：{k.get('domain', '')} {k.get('change', '')}")
    if sh.get("purpose"):
        out.append(f"  用途：{sh['purpose']}")
    if state is not None:
        f = fidelity_stats(state, sh["id"])
        if f["n"]:
            warn = " ⚠ 偏低，需修正或退役" if f["n"] >= 3 and f["mean"] < LOW_FIDELITY else ""
            out.append(f"  保真度：{f['mean']}（{f['n']} 次对比）{warn}")
    return "\n".join(out)


OUTPUT_SPEC = """{"interpretation": "你如何理解这个问题（用用户的话）",
 "approach": "你会怎么想/怎么做，按真实的思考顺序",
 "answer": "你最终的回答，用用户的表达风格",
 "confidence": 0.0-1.0,
 "uncertainties": ["你拿不准的地方"],
 "likely_errors": ["以你的水平最可能犯的错（含已有误区）"],
 "questions": ["你会追问或想查的"],
 "self_check": {"beyond_core": false, "notes": "是否用到了超出共同内核的知识/能力；如有，指出并改回"}}"""


def build_brief(state: dict, sh: dict, run: dict) -> str:
    core = state["core"]
    label, focus, qs = lens_text(sh["lens"])
    t = sh.get("time") or {"mode": "present"}
    parts = [f"# 影子模拟简报：{sh['id']}「{sh.get('name', label)}」（模拟 {run['id']}）", "",
             "你要模拟用户本人的一个「影子」。影子与用户共享同一个认知内核：同样的智力与认知水平、同样的知识边界、"
             "同样的误区和表达习惯；不同的只是观察问题的角度、所处的场景或时间。"
             "你是在预测「用户从这个角度会怎么想」，不是给出你自己的最佳答案。", "",
             "## 共同内核（不可改变）", "", render_core(core), "",
             "## 你的视角", "", f"{label}：{focus}"]
    if qs:
        parts.append("你会自然地问：" + " ".join(qs))
    sc = sh.get("scenario")
    if sc:
        parts += ["", "## 场景", "", (sc.get("setting") or SCENARIOS.get(sc.get("key", ""), ""))
                  + (f"；状态：{sc['state']}" if sc.get("state") else "")
                  + (f"；限制：{'、'.join(sc['constraints'])}" if sc.get("constraints") else "")]
    if t.get("mode") != "present":
        parts += ["", "## 时间", "", f"你是用户 {t['offset']} 的样子（{t['mode']}）。"]
        if t.get("assumptions"):
            parts.append("假设：" + "；".join(t["assumptions"]))
        for k in sh.get("knowledge_overrides") or []:
            parts.append(f"- 知识变化：{k.get('domain', '')} {k.get('change', '')}（{k.get('reason', '')}）")
    if sh.get("adjustments"):
        parts += ["", "## 与用户本人的细微差异", ""]
        parts += [f"- {a['trait']} {a['direction']}{a.get('magnitude', 1)}" + (f"：{a['note']}" if a.get("note") else "")
                  for a in sh["adjustments"]]
    if sh.get("voice"):
        parts += ["", f"语气：{sh['voice']}"]
    parts += ["", "## 规则", "",
              "1. 能力上限与用户一致：不使用用户不会的知识、方法或术语；遇到内核标为「不会」的内容，"
              "像用户那样卡住、猜测或绕开，并在 uncertainties 里写明。",
              "2. 保留 active 状态的误区：在相关问题上按误区去想（除非时间设定说明已纠正）。",
              "3. 用用户的表达风格和篇幅作答，不要写得比用户更规范、更完整。",
              "4. 视角只改变「先注意到什么、重视什么」，不改变「能想到多深」。",
              "5. 内核的数据缺口处要保守，并降低 confidence。",
              "6. 不要参考其他影子的输出。",
              "", "## 问题", "", run["problem"]]
    if run.get("context"):
        parts += ["", f"背景：{run['context']}"]
    parts += ["", "## 输出（JSON）", "", OUTPUT_SPEC]
    return "\n".join(parts)


def render_run(state: dict, r: dict) -> str:
    out = [f"# 模拟 {r['id']}：{r['problem']}", ""]
    if r.get("purpose"):
        out.append(f"目的：{r['purpose']}\n")
    out += ["| 影子 | 视角 | 置信度 | 回答（摘要） |", "|---|---|---|---|"]
    for sid in r["shadow_ids"]:
        o = r["outputs"].get(sid)
        sh = next((s for s in state["shadows"] if s["id"] == sid), {"id": sid, "lens": {}})
        label = lens_text(sh.get("lens") or {})[0]
        if o:
            ans = re.sub(r"\s+", " ", str(o.get("answer", "")))[:80]
            out.append(f"| {sid} {sh.get('name', '')} | {label} | {o.get('confidence', '')} | {ans} |")
        else:
            out.append(f"| {sid} {sh.get('name', '')} | {label} | — | （待模拟） |")
    for sid in r["shadow_ids"]:
        o = r["outputs"].get(sid)
        if not o:
            continue
        out += ["", f"## {sid}", ""]
        for k, label in (("interpretation", "理解"), ("approach", "思路"), ("answer", "回答")):
            if o.get(k):
                out.append(f"**{label}**：{o[k]}")
        for k, label in (("uncertainties", "不确定"), ("likely_errors", "可能的错误"), ("questions", "追问")):
            if o.get(k):
                out.append(f"**{label}**：" + "；".join(o[k]))
        sc = o.get("self_check") or {}
        if sc.get("beyond_core"):
            out.append(f"⚠ 自检：超出内核 — {sc.get('notes', '')}")
    syn = r.get("synthesis")
    if syn:
        out += ["", "## 综合", ""]
        for k, label in (("consensus", "共识"), ("divergences", "分歧"), ("blind_spots", "共同盲点"),
                         ("insights", "启发"), ("recommendations", "建议")):
            v = syn.get(k)
            if v:
                out.append(f"**{label}**：" + ("；".join(map(str, v)) if isinstance(v, list) else str(v)))
        pred = syn.get("prediction")
        if pred:
            out.append(f"**预测用户最可能的想法**：{pred.get('most_likely', '')}"
                       + (f"（置信度 {pred['confidence']}）" if pred.get("confidence") is not None else ""))
            for alt in pred.get("alternatives", []):
                out.append(f"  - 其他可能：{alt}")
    cmp_ = r.get("comparison")
    if cmp_:
        out += ["", "## 与用户真实想法对比", "", f"用户实际：{cmp_['actual']}"]
        for sid, f in cmp_["fidelity"].items():
            out.append(f"- {sid} 保真度 {f['score']}" + (f"：{f['notes']}" if f.get("notes") else ""))
        if cmp_.get("lessons"):
            out.append("教训：" + "；".join(cmp_["lessons"]))
    return "\n".join(out)


# ------------------------------------------------------------------ commands

def cmd_init(args):
    path = state_path(args)
    with locked(path):
        if path.exists():
            st = load(path)
            print(f"已存在：{path}（{len(st['shadows'])} 个影子，{len(st['runs'])} 次模拟）")
            return
        t = now()
        save(path, migrate({"schema_version": SCHEMA_VERSION, "profile_id": path.stem, "created_at": t}))
    print(f"已创建：{path}")


def cmd_core(args):
    path = state_path(args)
    if args.action == "show":
        state = load(path)
        if not state["core"]:
            raise ShadowError("还没有共同内核；先用 core set 建立")
        emit(args, state["core"], render_core(state["core"]))
        return
    data = read_payload(args)
    errs = check_core(data)
    if errs:
        raise ShadowError("\n  ".join(["共同内核校验失败："] + errs))
    with locked(path):
        state = load(path)
        if state["core"]:
            state["core_history"].append({"replaced_at": now(), "core": state["core"],
                                          "reason": args.reason or ""})
            state["core_history"] = state["core_history"][-50:]
        data["updated_at"] = now()
        state["core"] = data
        save(path, state)
    n = len(active_shadows(state))
    print(f"共同内核已{'更新' if state['core_history'] else '建立'}" +
          (f"；{n} 个影子将在下次模拟时自动使用新内核" if n else ""))


def cmd_create(args):
    path = state_path(args)
    data = read_payload(args)
    items = data if isinstance(data, list) else [data]
    with locked(path):
        state = load(path)
        if not state["core"]:
            raise ShadowError("先用 core set 建立共同内核，再创建影子")
        created = []
        for item in items:
            if not isinstance(item, dict):
                raise ShadowError("每个影子必须是 JSON 对象")
            sh = {k: v for k, v in item.items() if k != "id"}
            sh.setdefault("time", {"mode": "present"})
            sh["status"] = "active"
            state["counters"]["shadow"] += 1
            sh["id"] = f"sh{state['counters']['shadow']:02d}"
            sh.setdefault("name", lens_text(sh.get("lens") or {})[0])
            sh["created_at"] = now()
            errs = check_shadow(sh)
            if errs:
                raise ShadowError("\n  ".join([f"影子「{sh.get('name')}」校验失败："] + errs))
            state["shadows"].append(sh)
            created.append(sh)
        warnings = diversity_warnings(active_shadows(state))
        save(path, state)
    emit(args, {"created": [s["id"] for s in created], "warnings": warnings},
         "\n".join([f"已创建 {s['id']}「{s['name']}」" for s in created] + [f"! {w}" for w in warnings]))


def diversity_warnings(shadows: list[dict]) -> list[str]:
    seen: dict[tuple, str] = {}
    warns = []
    for s in shadows:
        t = s.get("time") or {}
        sig = ((s.get("lens") or {}).get("key") or (s.get("lens") or {}).get("focus"),
               (s.get("scenario") or {}).get("key") or (s.get("scenario") or {}).get("setting"),
               t.get("mode"), t.get("offset"))
        if sig in seen:
            warns.append(f"{s['id']} 与 {seen[sig]} 的视角/场景/时间完全相同，模拟结果会高度重复")
        seen[sig] = s["id"]
    return warns


def cmd_update(args):
    path = state_path(args)
    data = read_payload(args)
    with locked(path):
        state = load(path)
        sh = get_shadow(state, args.id)
        new = {**sh, **{k: v for k, v in data.items() if k not in ("id", "created_at")}, "updated_at": now()}
        errs = check_shadow(new)
        if errs:
            raise ShadowError("\n  ".join(["校验失败："] + errs))
        sh.clear()
        sh.update(new)
        save(path, state)
    print(f"已更新 {sh['id']}")


def cmd_retire(args):
    path = state_path(args)
    with locked(path):
        state = load(path)
        sh = get_shadow(state, args.id)
        sh["status"] = "retired"
        sh["notes"] = (sh.get("notes", "") + f"\n退役（{now()[:10]}）：{args.reason or ''}").strip()
        save(path, state)
    print(f"已退役 {sh['id']}（历史模拟记录保留）")


def cmd_list(args):
    state = load(state_path(args))
    shs = state["shadows"] if args.all else active_shadows(state)
    emit(args, shs, "\n".join(render_shadow(s, state) for s in shs) or "（还没有影子）")


def cmd_lenses(args):
    lines = ["内置视角（lens.key）："]
    lines += [f"- {k}: {v[0]} — {v[1]}" for k, v in LENSES.items()]
    lines += ["", "内置场景（scenario.key）："] + [f"- {k}: {v}" for k, v in SCENARIOS.items()]
    lines += ["", "时间（time.mode）：past（过去的自己，需 offset）/ present / future（需 offset 与 assumptions）"]
    emit(args, {"lenses": LENSES, "scenarios": SCENARIOS, "time_modes": TIME_MODES}, "\n".join(lines))


def cmd_run(args):
    path = state_path(args)
    act = args.action
    if act == "start":
        data = read_payload(args)
        if not isinstance(data, dict) or not str(data.get("problem", "")).strip():
            raise ShadowError("run start 需要 problem")
        with locked(path):
            state = load(path)
            if not state["core"]:
                raise ShadowError("还没有共同内核")
            ids = data.get("shadows") or [s["id"] for s in active_shadows(state)]
            shs = [get_shadow(state, i) for i in ids]
            if not shs:
                raise ShadowError("没有可用的影子；先 create")
            state["counters"]["run"] += 1
            run = {"id": f"r{state['counters']['run']:03d}", "created_at": now(), "problem": data["problem"],
                   "context": data.get("context", ""), "purpose": data.get("purpose", ""),
                   "shadow_ids": [s["id"] for s in shs], "core_snapshot": state["core"],
                   "outputs": {}, "synthesis": None, "comparison": None}
            state["runs"].append(run)
            save(path, state)
        briefs = {s["id"]: build_brief(state, s, run) for s in shs}
        if args.brief_dir:
            d = Path(args.brief_dir)
            d.mkdir(parents=True, exist_ok=True)
            for sid, b in briefs.items():
                (d / f"{run['id']}-{sid}.md").write_text(b, encoding="utf-8")
        emit(args, {"run": run["id"], "briefs": briefs},
             f"模拟 {run['id']} 已创建，{len(shs)} 个影子："
             + ("" if not args.brief_dir else f" 简报已写入 {args.brief_dir}/")
             + ("\n\n" + "\n\n---\n\n".join(briefs.values()) if not args.brief_dir else ""))
        return
    with locked(path):
        state = load(path)
        run = get_run(state, args.run)
        if act == "brief":
            sids = [get_shadow(state, args.shadow)["id"]] if args.shadow else run["shadow_ids"]
            print("\n\n---\n\n".join(build_brief({**state, "core": run["core_snapshot"]},
                                                 get_shadow(state, s), run) for s in sids))
            return
        if act == "show":
            emit(args, run, render_run(state, run))
            return
        data = read_payload(args)
        if act == "add":
            sh = get_shadow(state, args.shadow or "")
            if sh["id"] not in run["shadow_ids"]:
                raise ShadowError(f"{sh['id']} 不在模拟 {run['id']} 中")
            if not isinstance(data, dict) or not data.get("answer"):
                raise ShadowError("影子输出至少需要 answer")
            c = data.get("confidence")
            if c is not None and not (isinstance(c, (int, float)) and 0 <= c <= 1):
                raise ShadowError("confidence 应为 0-1")
            data["recorded_at"] = now()
            run["outputs"][sh["id"]] = data
            msg = f"已记录 {sh['id']} 在 {run['id']} 中的思考"
            if (data.get("self_check") or {}).get("beyond_core"):
                msg += "\n! 自检发现超出共同内核：请按内核修正后重新 add（会覆盖）"
            missing = [s for s in run["shadow_ids"] if s not in run["outputs"]]
            msg += f"（还差 {', '.join(missing)}）" if missing else "（全部影子已完成，可以 synthesize）"
        elif act == "synthesize":
            missing = [s for s in run["shadow_ids"] if s not in run["outputs"]]
            if missing:
                raise ShadowError(f"还有影子未模拟：{missing}")
            allowed = {"consensus", "divergences", "blind_spots", "insights", "prediction", "recommendations"}
            if not isinstance(data, dict) or set(data) - allowed:
                raise ShadowError(f"synthesis 只能包含 {sorted(allowed)}")
            if not data.get("divergences"):
                raise ShadowError("synthesis 需要 divergences：多视角模拟的价值就在于分歧")
            data["recorded_at"] = now()
            run["synthesis"] = data
            msg = f"已记录 {run['id']} 的综合"
        elif act == "compare":
            if not isinstance(data, dict) or not data.get("actual"):
                raise ShadowError("compare 需要 actual（用户真实的想法或回答，逐字）")
            fid = data.get("fidelity") or {}
            for sid, f in fid.items():
                if sid not in run["outputs"]:
                    raise ShadowError(f"{sid} 在 {run['id']} 中没有输出")
                if not isinstance(f, dict) or not (isinstance(f.get("score"), (int, float)) and 0 <= f["score"] <= 1):
                    raise ShadowError(f"fidelity.{sid}.score 应为 0-1")
            if set(run["outputs"]) - set(fid):
                raise ShadowError(f"每个影子都要给保真度，缺少 {sorted(set(run['outputs']) - set(fid))}")
            run["comparison"] = {"actual": data["actual"], "fidelity": fid, "closest": data.get("closest", ""),
                                 "lessons": data.get("lessons") or [], "recorded_at": now()}
            low = [s for s in fid if fidelity_stats(state, s)["n"] >= 3 and fidelity_stats(state, s)["mean"]
                   < LOW_FIDELITY]
            msg = f"已记录 {run['id']} 的对比" + (f"\n! 保真度持续偏低：{low}，考虑 update 或 retire" if low else "")
        else:  # pragma: no cover
            raise ShadowError(act)
        save(path, state)
    print(msg)


def cmd_runs(args):
    state = load(state_path(args))
    lines = []
    for r in state["runs"][-args.limit:]:
        status = "已对比" if r.get("comparison") else "已综合" if r.get("synthesis") else \
            f"{len(r['outputs'])}/{len(r['shadow_ids'])}"
        problem = re.sub(r"\s+", " ", r["problem"])[:60]
        lines.append(f"{r['id']} {r['created_at'][:10]} [{status}] {problem}")
    emit(args, state["runs"][-args.limit:], "\n".join(lines) or "（无模拟记录）")


def cmd_report(args):
    state = load(state_path(args))
    parts = ["# 影子用户报告", "", "## 共同内核", "", render_core(state["core"]) if state["core"] else "（未建立）",
             "", "## 影子", ""]
    parts += [render_shadow(s, state) for s in state["shadows"]]
    runs = [get_run(state, args.run)] if args.run else state["runs"][-args.recent:]
    for r in runs:
        parts += ["", render_run(state, r)]
    text = "\n".join(parts)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"已写入 {args.output}")
    else:
        print(text)


def cmd_validate(args):
    state = load(state_path(args))
    errs = validate_state(state)
    print("\n".join(errs) if errs else f"✓ 状态有效（{len(state['shadows'])} 个影子，{len(state['runs'])} 次模拟）")
    return 1 if errs else 0


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", "-p", help="影子组名（默认 $SHADOW_USERS_PROFILE 或 default）")
    common.add_argument("--store", help="存储目录（默认 $SHADOW_USERS_HOME 或 ~/.local/share/shadow-users）")
    common.add_argument("--json", action="store_true", help="输出 JSON")
    common.add_argument("--data", "-d", help="JSON 字符串（默认从 stdin 读取）")
    common.add_argument("--file", "-f", help="从文件读取 JSON")
    p = argparse.ArgumentParser(prog="shadows.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, parents=[common], help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    add("init", cmd_init, "创建状态文件")
    sp = add("core", cmd_core, "查看/设置共同内核（所有影子共享的认知水平、知识边界、误区、风格）")
    sp.add_argument("action", choices=["show", "set"])
    sp.add_argument("--reason", help="替换内核的原因（旧内核保留在 core_history）")
    add("create", cmd_create, "创建一个或多个影子（JSON 对象或列表）")
    sp = add("update", cmd_update, "修改影子（合并字段）")
    sp.add_argument("id")
    sp = add("retire", cmd_retire, "退役影子")
    sp.add_argument("id")
    sp.add_argument("--reason")
    sp = add("list", cmd_list, "列出影子（含保真度）")
    sp.add_argument("--all", action="store_true", help="包含已退役的")
    add("lenses", cmd_lenses, "列出内置视角与场景")

    sp = add("run", cmd_run, "多影子模拟：start / brief / add / synthesize / compare / show")
    sp.add_argument("action", choices=["start", "brief", "add", "synthesize", "compare", "show"])
    sp.add_argument("run", nargs="?", help="模拟 id（start 以外必填）")
    sp.add_argument("shadow", nargs="?", help="影子 id（add 必填，brief 可选）")
    sp.add_argument("--brief-dir", help="start 时把每个影子的简报写成文件（便于分发给子代理）")

    sp = add("runs", cmd_runs, "列出模拟记录")
    sp.add_argument("--limit", type=int, default=30)
    sp = add("report", cmd_report, "生成 Markdown 报告")
    sp.add_argument("--run", help="只报告某次模拟")
    sp.add_argument("--recent", type=int, default=5)
    sp.add_argument("--output", "-o")
    add("validate", cmd_validate, "校验状态文件")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "run" and args.action != "start" and not args.run:
            raise ShadowError(f"run {args.action} 需要模拟 id")
        return args.fn(args) or 0
    except ShadowError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
