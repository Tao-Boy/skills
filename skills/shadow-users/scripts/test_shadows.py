"""Run: uv run --with pytest pytest skills/shadow-users/scripts -q"""

import json

import pytest

import shadows as S

CORE = {"name": "u", "source": "测试", "cognition": {"abstraction": 3, "rigor": {"level": 2, "evidence": "跳步"}},
        "knowledge": [{"domain": "物理/电磁学", "level": "熟练", "known": ["高斯定理"], "unknown": ["镜像法"]}],
        "misconceptions": [{"concept": "高斯定理求场条件", "detail": "只看曲面形状"}], "data_confidence": 0.4,
        "gaps": ["开放题样本少"]}


@pytest.fixture()
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SHADOW_USERS_HOME", str(tmp_path))

    def _run(*argv, data=None):
        args = list(argv) + (["--data", json.dumps(data, ensure_ascii=False)] if data is not None else [])
        rc = S.main(args)
        out = capsys.readouterr()
        return rc, out.out + out.err

    assert _run("init")[0] == 0
    return _run


def state(tmp_path):
    return json.loads((tmp_path / "default.json").read_text(encoding="utf-8"))


def test_core_required_and_validated(run):
    assert run("create", data={"lens": {"key": "skeptic"}})[0] == 2  # no core yet
    assert run("core", "set", data={"cognition": {"rigor": 9}})[0] == 2  # no source, bad rating
    assert run("core", "set", data=CORE)[0] == 0
    rc, out = run("core", "show")
    assert rc == 0 and "镜像法" in out


def test_capability_cannot_change_in_present(run, tmp_path):
    run("core", "set", data=CORE)
    bad = {"lens": {"key": "skeptic"}, "adjustments": [{"trait": "rigor", "direction": "+"}]}
    rc, out = run("create", data=[{"lens": {"key": "detail"}}, bad])
    assert rc == 2 and "能力维度" in out
    assert state(tmp_path)["shadows"] == []  # batch is atomic
    ok = {**bad, "time": {"mode": "future", "offset": "+1年", "assumptions": ["系统复习"]}}
    assert run("create", data=ok)[0] == 0
    assert run("create", data={"lens": {"key": "detail"},
                               "knowledge_overrides": [{"domain": "x", "change": "学会"}]})[0] == 2
    assert run("create", data={"lens": {"key": "detail"}, "time": {"mode": "future", "offset": "+1年"}})[0] == 2
    assert run("create", data={"lens": {"key": "nope"}})[0] == 2


def test_duplicate_shadow_warning(run):
    run("core", "set", data=CORE)
    run("create", data={"lens": {"key": "detail"}})
    rc, out = run("create", data={"lens": {"key": "detail"}})
    assert rc == 0 and "完全相同" in out


def test_simulation_lifecycle(run, tmp_path):
    run("core", "set", data=CORE)
    run("create", data=[{"name": "怀疑", "lens": {"key": "skeptic"}},
                        {"name": "考场", "lens": {"key": "pragmatist"}, "scenario": {"key": "exam"}}])
    rc, out = run("run", "start", "--brief-dir", str(tmp_path / "b"), data={"problem": "接地导体球外点电荷的场？"})
    assert rc == 0
    brief = (tmp_path / "b" / "r001-sh02.md").read_text(encoding="utf-8")
    assert "镜像法" in brief and "限时考试" in brief and "保留 active 状态的误区" in brief
    assert run("run", "synthesize", "r001", data={"divergences": ["a"]})[0] == 2  # outputs missing
    assert run("run", "add", "r001", "sh01", data={"answer": "不对称，用不了", "confidence": 0.3})[0] == 0
    assert run("run", "add", "r001", "sh02", data={"answer": "E=kq/r^2", "confidence": 1.5})[0] == 2
    assert run("run", "add", "r001", "2", data={"answer": "E=kq/r^2", "confidence": 0.8})[0] == 0
    assert run("run", "synthesize", "r001", data={"consensus": ["x"]})[0] == 2  # needs divergences
    assert run("run", "synthesize", "r001", data={"divergences": ["a"], "prediction": {"most_likely": "套高斯"}})[0] == 0
    assert run("run", "compare", "r001", data={"actual": "E=kq/r^2", "fidelity": {"sh01": {"score": 0.3}}})[0] == 2
    assert run("run", "compare", "r001", data={"actual": "E=kq/r^2", "fidelity": {
        "sh01": {"score": 0.3}, "sh02": {"score": 0.9}}})[0] == 0
    st = S.migrate(state(tmp_path))
    assert S.fidelity_stats(st, "sh02") == {"n": 1, "mean": 0.9}
    rc, out = run("report", "--run", "r001")
    assert "预测用户最可能的想法" in out and "保真度 0.9" in out


def test_core_update_keeps_history_and_run_snapshot(run, tmp_path):
    run("core", "set", data=CORE)
    run("create", data={"lens": {"key": "skeptic"}})
    run("run", "start", data={"problem": "q"})
    run("core", "set", "--reason", "新证据", data={**CORE, "summary": "更新后"})
    s = state(tmp_path)
    assert s["core_history"][0]["reason"] == "新证据"
    assert "summary" not in s["runs"][0]["core_snapshot"]
    rc, out = run("run", "brief", "r001")
    assert rc == 0 and "更新后" not in out  # brief uses the snapshot taken at start


def test_retire_and_validate(run):
    run("core", "set", data=CORE)
    run("create", data={"lens": {"focus": "从历史发展的角度看"}, "name": "史观"})
    assert run("retire", "sh01", "--reason", "保真度低")[0] == 0
    rc, out = run("list")
    assert "还没有影子" in out
    rc, out = run("list", "--all")
    assert "史观" in out
    assert run("validate")[0] == 0
