# 状态文件结构（schema v1）

一个 profile 对应一个 JSON 文件：`$USER_STATUS_HOME/<profile>.json`。旁边有：
- `backups/<profile>.<时间戳>.json`：每次写入前的快照，保留最近 20 份；
- `<profile>.lock`：防止并发写入的文件锁。

```jsonc
{
  "schema_version": 1,
  "profile_id": "default",
  "created_at": "2026-10-19T16:00:00+08:00", "updated_at": "…",
  "subject": {"name": "张三", "about": "物理系大二，准备期末"},   // 用户自述的背景
  "counters": {"record": 12, "session": 3},                    // id 生成器，只增不减（删除记录后 id 也不会复用）
  "rubric": {"version": 1, "scale": [0, 4],
             "dimensions": [{"key": "correctness", "label": "正确性", "weight": 0.4, "anchors": {"0": "…", "4": "…"}}]},
  "sessions": [
    {"id": "s001", "started_at": "…", "ended_at": "…", "mode": "quiz", "topic": "电磁学", "goal": "摸底", "summary": "…"}
  ],
  "records": [
    {
      "id": "q0001", "session_id": "s001", "asked_at": "…",
      "question": {
        "text": "…", "type": "concept", "topics": ["物理/电磁学/高斯定理"], "difficulty": 3,
        "intent": "提问目的", "expected_points": ["…"], "reference_answer": "…",
        "source": "出处（可选）", "parent": null, "tags": [], "context": "提问时的上下文（可选）"
      },
      "answers": [                                   // 按作答顺序，attempt 从 1 开始
        {"text": "用户原话", "answered_at": "…", "self_confidence": 0.8, "hints_used": 0, "latency_s": null, "notes": ""}
      ],
      "evaluations": [                               // 只追加；被修订的评估标记 superseded
        {"attempt": 1, "verdict": "partially_correct",
         "scores": {"correctness": 2, "completeness": 2}, "overall": 50.0,
         "strengths": ["…"], "issues": [{"type": "misconception", "concept": "…", "detail": "…"}],
         "evidence": ["回答原文片段"], "confidence": 0.85, "feedback": "…", "notes": "",
         "rubric_version": 1, "evaluated_at": "…",
         "superseded": true,                         // 仅在被修订后出现
         "revision_reason": "…"}                     // 仅出现在修订版上
      ],
      "followups": ["q0004"]                         // 以本题为 parent 的追问
    }
  ],
  "profile": {
    "summary": "…", "level": "…",
    "traits":          [{"text": "…", "evidence": ["q0001", "q0005"], "confidence": 0.6}],
    "preferences":     [{"text": "…", "evidence": ["q0003"]}],
    "goals":           [{"text": "…", "evidence": []}],
    "recommendations": [{"text": "…", "evidence": []}],
    "misconceptions":  [{"concept": "…", "status": "active|resolved|dismissed", "note": "…", "evidence": ["…"], "updated_at": "…"}],
    "history": [{"at": "…", "records": 12, "graded": 10, "mean": 71.5, "level": "…", "summary": "…", "topics": {"物理": 0.72}}],
    "updated_at": "…"
  }
}
```

统计数据（掌握度等）**不写入文件**，每次运行 `profile` 时根据 records 重新计算。因此算法改进之后，历史数据也会自动按新口径统计；
`profile.history` 保存的是当时的结果快照。

## 维护约定

- **只增不改**：问题和回答一旦记录就不再修改。重答时追加新的 answer，修订评估时追加新的 evaluation，并把旧的标记为 superseded。
  唯一的硬删除是 `delete`，在用户要求遗忘时使用，删除前的状态会留在备份里。
- **写前校验**：每次写入前，脚本都会检查 id 唯一、引用存在、各项取值在合法范围内，以及各种枚举值是否合法。
  校验不通过时拒绝写入，原文件保持不变。
- **原子写入**：先写临时文件，再 fsync，最后用 `os.replace` 替换原文件，写入中途中断也不会损坏文件。
- **版本迁移**：修改结构时，要把 `SCHEMA_VERSION` 加一，并在 `user_status.py` 的 `migrate()` 中补上 `if v < N` 分支。
  旧文件在下次被读取、写入时会自动升级；如果文件版本比脚本新，脚本会拒绝读取。
- **规模**：单个文件存放几千条记录没有问题。记录特别多时，可以按时间拆成多个 profile，例如 `-p 张三-2026上`。
