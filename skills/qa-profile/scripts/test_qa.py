"""Run: uv run --with pytest pytest skills/qa-profile/scripts -q"""

import json

import pytest

import qa


@pytest.fixture()
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QA_PROFILE_HOME", str(tmp_path))

    def _run(*argv, data=None):
        args = list(argv) + (["--data", json.dumps(data, ensure_ascii=False)] if data is not None else [])
        rc = qa.main(args)
        out = capsys.readouterr()
        return rc, out.out + out.err

    assert _run("init", "--name", "u")[0] == 0
    return _run


def state(tmp_path):
    return json.loads((tmp_path / "default.json").read_text(encoding="utf-8"))


Q = {"text": "高斯定理是什么？", "type": "concept", "topics": ["物理 / 电磁学 / 高斯定理"], "difficulty": 2,
     "expected_points": ["通量等于包围电荷除以ε0"]}
EV = {"verdict": "partially_correct", "scores": {"correctness": 2, "completeness": 2},
      "issues": [{"type": "misconception", "concept": "求场条件", "detail": "只看曲面形状"}],
      "evidence": ["只要是球面"]}


def test_full_flow_and_stats(run, tmp_path):
    run("session", "start", "--topic", "电磁学")
    assert run("ask", data=Q)[0] == 0
    assert run("answer", "q0001", "--text", "通量等于电荷除以ε0，只要是球面就能求场")[0] == 0
    rc, out = run("evaluate", "1", data=EV)
    assert rc == 0 and "!" not in out
    s = state(tmp_path)
    rec = s["records"][0]
    assert rec["session_id"] == "s001"
    assert rec["question"]["topics"] == ["物理/电磁学/高斯定理"]
    assert rec["evaluations"][0]["overall"] == 50.0  # (0.4*2/4 + 0.2*2/4) / 0.6
    st = qa.compute_stats(qa.migrate(s))
    assert set(st["topics"]) == {"物理", "物理/电磁学", "物理/电磁学/高斯定理"}
    assert st["misconceptions"][0]["concept"] == "求场条件"
    assert st["topics"]["物理"]["level"] == "数据不足"


def test_evidence_must_quote_answer(run):
    run("ask", data=Q)
    run("answer", "q0001", "--text", "不知道细节")
    rc, out = run("evaluate", "q0001", data={**EV, "evidence": ["编造的引用"]})
    assert rc == 0 and "不是回答原文的片段" in out


def test_double_evaluate_requires_revise(run, tmp_path):
    run("ask", data=Q)
    run("answer", "q0001", "--text", "只要是球面")
    run("evaluate", "q0001", data=EV)
    assert run("evaluate", "q0001", data=EV)[0] == 2
    assert run("revise", "q0001", data=EV)[0] == 2  # missing reason
    assert run("revise", "q0001", data={**EV, "verdict": "incorrect", "reason": "复核"})[0] == 0
    evs = state(tmp_path)["records"][0]["evaluations"]
    assert evs[0]["superseded"] and evs[1]["verdict"] == "incorrect"


def test_validation_rejects_bad_input(run):
    assert run("ask", data={"text": ""})[0] == 2
    assert run("ask", data={**Q, "type": "nope"})[0] == 2
    assert run("ask", data={**Q, "difficulty": 9})[0] == 2
    run("ask", data=Q)
    assert run("evaluate", "q0001", data=EV)[0] == 2  # no answer yet
    run("answer", "q0001", "--text", "x")
    assert run("evaluate", "q0001", data={**EV, "scores": {"correctness": 7}})[0] == 2
    assert run("evaluate", "q0001", data={"verdict": "correct"})[0] == 2  # graded needs scores


def test_log_preference_and_profile_update(run, tmp_path):
    rc, _ = run("log", data=[
        {"question": {"text": "喜欢先例题还是推导？", "type": "preference"}, "answer": {"text": "先例题"},
         "evaluation": {"verdict": "not_applicable", "evidence": ["先例题"]}},
        {"question": Q, "answer": {"text": "只要是球面", "self_confidence": 0.9}, "evaluation": EV}])
    assert rc == 0
    assert run("profile-update", data={"summary": "s", "traits": [{"text": "t", "evidence": ["q9999"]}]})[0] == 2
    assert run("profile-update", data={"summary": "s", "preferences": [{"text": "先例题", "evidence": ["2"]}],
                                       "misconceptions": [{"concept": "求场条件", "status": "resolved"}]})[0] == 0
    s = state(tmp_path)
    assert s["profile"]["preferences"][0]["evidence"] == ["q0002"]
    assert len(s["profile"]["history"]) == 1
    st = qa.compute_stats(qa.migrate(s))
    assert st["misconceptions"][0]["status"] == "resolved"
    rc, out = run("profile")
    assert "用户画像" in out and "求场条件" in out


def test_delete_cleans_references_and_backups(run, tmp_path):
    run("ask", data=Q)
    run("ask", data={**Q, "parent": "q0001"})
    assert run("delete", "q0001")[0] == 0
    s = state(tmp_path)
    assert [r["id"] for r in s["records"]] == ["q0002"] and s["records"][0]["question"]["parent"] is None
    assert list((tmp_path / "backups").glob("default.*.json"))
    assert run("validate")[0] == 0


def test_corrupt_file_is_reported(run, tmp_path):
    (tmp_path / "default.json").write_text("{broken", encoding="utf-8")
    rc, out = run("list")
    assert rc == 2 and "backups" in out


def test_verdict_score_consistency_warning(run):
    run("ask", data=Q)
    run("answer", "q0001", "--text", "只要是球面")
    rc, out = run("evaluate", "q0001", data={**EV, "verdict": "correct", "scores": {"correctness": 1}})
    assert rc == 0 and "不一致" in out
