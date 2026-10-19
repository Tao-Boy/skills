---
name: user-status
description: 详细记录用户对 Agent 所提问题的回答（逐字保存原话、作答次数、自评把握度），由 Agent 按评分标准评估答案质量并给出依据，再据此持续维护用户画像（知识掌握度、能力维度、误区、特征、偏好、学习建议）。所有状态保存在一个 JSON 文件中，由脚本负责校验、备份和迁移，可以跨会话持续积累。当用户要 Agent 考考自己、出题测验、做面试或问卷、进行苏格拉底式辅导，要求记录或回顾自己的回答，或者询问「我掌握得怎么样」「给我画像」「我的薄弱点是什么」时使用。
---

# 问答记录与用户画像（user-status）

工作方式分为四步：**Agent 提问 → 逐字记录用户回答 → Agent 依据证据评估 → 汇总成用户画像**。
所有状态集中在一个 JSON 文件里，由 `scripts/user_status.py` 负责读写。不要手动编辑 JSON：脚本会做字段校验、
加文件锁、原子写入、自动备份（保留最近 20 份）和版本迁移，这样状态才能长期、可靠地维护下去。

## 原则（必须遵守）

1. **原话逐字记录**：`answer.text` 必须是用户的原话，不要改写、概括或纠错。用户补充或重答时，再调用一次
   `answer`，会作为新的一次作答保存。
2. **先定标准，再看回答**：调用 `ask` 时就写好 `expected_points`（期望要点）和 `intent`（提问目的），
   然后才向用户提问，避免看到回答之后再调整标准。
3. **评估要有证据**：每个判断都要在 `evidence` 中逐字引用回答原文（脚本会检查引文是否真的出自回答）。
   分数按 `references/rubric.md` 中的锚点给出；拿不准时降低 `confidence`，不要硬判。
4. **区分偶然失误与真正误区**：一次计算失误记为 `calculation_slip`。只有能从回答中看出错误的心智模型时，
   才记为 `misconception`。
5. **画像要克制**：画像只描述与问答相关的能力、知识、学习方式和偏好，**不推断**健康、政治、宗教、性取向、
   民族等敏感属性，也不对人格下结论。样本少于 2 条的主题标为「数据不足」。每条特征都要附上证据记录 id。
6. **保持透明**：开始记录前告诉用户回答会被保存并用于画像；用户可以随时查看画像（`profile`）、
   对评估提出异议（用 `revise` 修订，旧评估会保留）、要求删除记录（`delete`）。

## 运行方式

```bash
STATUS="uv run <本SKILL.md所在目录>/scripts/user_status.py"     # 零依赖；没有 uv 时也可以用 python3 运行
$STATUS --help
```

- 状态文件是 `$USER_STATUS_HOME/<profile>.json`，默认路径为 `~/.local/share/user-status/default.json`。
  每个被记录的人用一个 profile（`-p 名字`，或设置 `$USER_STATUS_PROFILE`）。
- 结构化输入一律用 heredoc 通过 stdin 传入 JSON（`<<'EOF'` 要加引号，这样 LaTeX 里的 `\` 和 `$` 不会被 shell 展开）。
- 加 `--json` 可以得到结构化输出。

## 流程

### 0. 准备（每次对话开始时）

```bash
$STATUS init --name "<称呼>" --about "<用户自述的背景>"     # 文件已存在时不会重复创建
$STATUS profile                                            # 读取现有画像，据此决定问什么、难度多高
$STATUS list --pending                                     # 查看上次遗留的待回答或待评估记录
$STATUS session start --mode quiz --topic "电磁学" --goal "摸底高斯定理"
```

mode 取以下值之一：quiz（测验）/ interview（面试）/ tutoring（辅导）/ survey（问卷）/ review（复习）/ other。

### 1. 提问前先记录问题

```bash
$STATUS ask <<'EOF'
{"text": "高斯定理在什么条件下能方便地求出电场？", "type": "concept",
 "topics": ["物理/电磁学/高斯定理"], "difficulty": 3,
 "intent": "检验是否理解对称性要求",
 "expected_points": ["定理对任意闭合曲面都成立", "求场需要电荷分布高度对称", "能把 E 提到积分外"],
 "parent": null}
EOF
```

- `type`：concept / calculation / proof / application / factual / open / design 这些类型要评分；
  opinion / preference / background 不评分，只用于画像中的偏好和背景。
- `topics` 用 `/` 分层（领域/分支/知识点），统计时会逐层汇总。同一个知识点每次都要用同一个名称。
- `difficulty` 取 1–5。追问时用 `parent` 指向原问题。
- 然后把问题发给用户。题目要根据画像自适应：薄弱的主题多问，已经掌握的主题提高难度；同一个误区隔一段时间再复查。

### 2. 记录回答

```bash
$STATUS answer q0007 <<'EOF'
{"text": "<用户原话，逐字>", "self_confidence": 0.8, "hints_used": 0}
EOF
# 或者简单写成：$STATUS answer q0007 --text "<原话>"
```

`self_confidence`（用户自评的把握度，0–1）可选，但很有价值：它能用来判断用户是偏自信还是偏保守。需要时可以先问一句「你有几成把握？」。
如果给过提示，记在 `hints_used` 里。

### 3. 评估

```bash
$STATUS evaluate q0007 <<'EOF'
{"verdict": "partially_correct",
 "scores": {"correctness": 2, "completeness": 2, "reasoning": 1, "clarity": 3, "precision": 3},
 "strengths": ["定理表述正确"],
 "issues": [{"type": "misconception", "concept": "高斯定理求场条件",
             "detail": "认为只要取球面就能求场，忽略了电荷分布的对称性"}],
 "evidence": ["只要曲面是球面就能用它求电场"],
 "confidence": 0.85,
 "feedback": "定理本身没错，但用它求场需要电荷分布高度对称……"}
EOF
```

- 评分锚点、verdict 和 issue 类型的定义见 `references/rubric.md`。`overall`（0–100）默认按 rubric 权重自动计算。
- 每次评估只针对一次作答（`attempt`，默认是最近一次）。多次作答时，可以分别评估每一次。
- 如果出现「evidence 不是回答原文」的警告，说明引文不准确，要用 `revise` 改正。
- 用户提出异议，或你发现自己判错了：用 `$STATUS revise q0007`，提交同样格式的 JSON，并加上 `"reason"` 说明修改原因。
- `feedback` 是实际告诉用户的反馈，要和评估结论一致。评估结果通常应当反馈给用户（问卷或面试场景可以按需省略）。

事后补录时，可以用 `$STATUS log` 一次提交 `{question, answer, evaluation}`，也可以提交它们组成的列表。

### 4. 更新画像

每完成 5 题左右、会话结束时，或者用户询问画像时：

```bash
$STATUS profile                       # 先看统计：掌握度、维度、误区、自信校准、趋势
$STATUS profile-update <<'EOF'
{"summary": "2–4 句总体评价，基于统计和具体记录",
 "level": "本科中等",
 "traits": [{"text": "倾向于先给结论、推导简略", "evidence": ["q0001", "q0005"], "confidence": 0.6}],
 "preferences": [{"text": "先看例题再看推导", "evidence": ["q0003"]}],
 "goals": [{"text": "期末前掌握静电场边值问题", "evidence": ["q0002"]}],
 "misconceptions": [{"concept": "高斯定理求场条件", "status": "resolved", "note": "q0009 已能正确说明", "evidence": ["q0009"]}],
 "recommendations": ["多练需要判断对称性的题目", "推导时写出中间步骤"]}
EOF
$STATUS session end --summary "本次覆盖高斯定理 4 题，误区已纠正"
```

- 统计部分由脚本自动计算，不需要手写：
  - 各主题掌握度：按时间加权，越近的权重越大，难题权重更高；
  - 各维度均分、判定分布、问题类型统计；
  - 误区：从评估中按 concept 汇总；
  - 自信校准：比较自评把握度与实际得分；
  - 近 5 题与前 5 题的对比。
- Agent 只撰写需要判断的部分：summary、level、traits、preferences、goals、recommendations，以及误区的状态
  （active / resolved / dismissed）。traits、preferences、goals、recommendations 每次都是**整体替换**，所以要提交完整列表。
- 每次更新都会保存一份快照（`profile.history`），可以据此看出画像的变化过程。
- 写画像的方法见 `references/profiling.md`。

### 5. 查看与维护

```bash
$STATUS list [--topic 电磁学] [--verdict incorrect] [--issue misconception] [--since 2026-10-01] [--pending]
$STATUS show q0007 q0008
$STATUS report -o report.md [--recent 20]     # 完整 Markdown 报告：画像 + 演变 + 问答记录
$STATUS validate                              # 校验状态文件
$STATUS delete q0003                          # 用户要求遗忘时使用；删除前的状态会留在 backups/
$STATUS rubric show | $STATUS rubric set          # 查看或替换评分标准（已经用过的维度必须保留）
```

状态文件损坏时，脚本会拒绝读取，并提示从 `backups/` 目录恢复。把最新的完好备份复制回原文件名即可。

## 参考文件

- `references/rubric.md`：评分维度和锚点、verdict 定义、issue 类型、评估示例
- `references/profiling.md`：如何从记录归纳画像、证据要求、禁止推断的内容、自适应出题
- `references/schema.md`：JSON 状态文件的结构和版本迁移约定
