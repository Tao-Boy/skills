# 状态文件结构（schema v1）

一个 profile 对应一个文件：`$SHADOW_USERS_HOME/<profile>.json`。旁边有 `backups/`（每次写入前的快照，保留最近 20 份）
和 `<profile>.lock`（防止并发写入的文件锁）。

```jsonc
{
  "schema_version": 1,
  "profile_id": "default",
  "created_at": "…", "updated_at": "…",
  "counters": {"shadow": 4, "run": 7},          // id 生成器：sh01…、r001…
  "core": {                                      // 共同内核，字段见 designing-shadows.md
    "name": "…", "source": "…", "summary": "…",
    "cognition": {"abstraction": 3, "rigor": {"level": 2, "evidence": "…"}, "notes": "…"},
    "knowledge": [{"domain": "…", "level": "…", "known": [], "unknown": []}],
    "misconceptions": [{"concept": "…", "detail": "…", "status": "active"}],
    "thinking_style": [], "expression": {}, "values": [], "goals": [], "context": "…",
    "data_confidence": 0.5, "gaps": [], "updated_at": "…"
  },
  "core_history": [{"replaced_at": "…", "reason": "…", "core": {…}}],   // 最近 50 版旧内核
  "shadows": [
    {"id": "sh01", "name": "怀疑的我", "status": "active|retired",
     "lens": {"key": "skeptic", "focus": "（自定义时填写）", "questions": [], "label": "（可选）"},
     "scenario": {"key": "exam", "setting": "（自定义时填写）", "state": "紧张", "constraints": ["10 分钟"]},
     "time": {"mode": "present|past|future", "offset": "-3个月", "assumptions": []},
     "adjustments": [{"trait": "质疑倾向", "direction": "+", "magnitude": 1, "note": "…"}],
     "knowledge_overrides": [{"domain": "…", "change": "…", "reason": "…"}],   // 仅 past/future
     "voice": "…", "purpose": "…", "notes": "…", "created_at": "…", "updated_at": "…"}
  ],
  "runs": [
    {"id": "r001", "created_at": "…", "problem": "…", "context": "…", "purpose": "…",
     "shadow_ids": ["sh01", "sh02"],
     "core_snapshot": {…},                       // 开始模拟时的内核快照（brief 会用它）
     "outputs": {"sh01": {"interpretation": "…", "approach": "…", "answer": "…", "confidence": 0.3,
                          "uncertainties": [], "likely_errors": [], "questions": [],
                          "self_check": {"beyond_core": false, "notes": ""}, "recorded_at": "…"}},
     "synthesis": {"consensus": [], "divergences": [], "blind_spots": [], "insights": [],
                   "prediction": {"most_likely": "…", "confidence": 0.6, "alternatives": []},
                   "recommendations": [], "recorded_at": "…"},
     "comparison": {"actual": "用户原话", "fidelity": {"sh01": {"score": 0.4, "notes": "…"}},
                    "closest": "sh02", "lessons": [], "recorded_at": "…"}}
  ]
}
```

保真度统计（每个影子的平均分和对比次数）不写入文件，每次运行 `list` 或 `report` 时根据 `runs[].comparison` 重新计算。

## 维护约定

- **写前校验**：每次写入前都会检查内核和影子的字段与取值范围，也会检查「当下的影子不能调整能力维度」这条规则，以及引用的影子是否存在。
  校验不通过时拒绝写入。批量创建是原子的：只要有一个影子不合格，整批都不会写入。
- **原子写入和备份**：先写临时文件，再 fsync，最后用 `os.replace` 替换原文件。文件损坏时，脚本会提示从 `backups/` 恢复。
- **可追溯**：替换内核时，旧版本会进入 `core_history`；影子只退役、不删除；每次模拟都保存当时的内核快照。
- **版本迁移**：修改结构时，要把 `SCHEMA_VERSION` 加一，并在 `shadows.py` 的 `migrate()` 中补上升级分支。
