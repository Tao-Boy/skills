---
name: shadow-users
description: 根据用户画像创建一个或多个「影子用户」。影子与用户拥有相近的智力和认知水平、相同的知识边界和误区，但各自从不同的角度观察问题（怀疑、直觉、细节、实用、讲解、他人视角……），也可以放在不同场景（考场、疲惫、讨论）或不同时间点（过去的自己、几个月后的自己）中，用来模拟用户在多角度、多场景、多时间下对问题的思考，并通过与用户真实想法的对比不断提高保真度。当用户想看看「我会怎么想」「从不同角度的我会怎么看」、预测自己在某种情境下的反应、做自我反思或决策推演，或者教学者想预判学生的思路和卡点时使用。
---

# 影子用户（shadow-users）

影子用户是 Agent 对用户本人的**多视角模拟**。所有影子共享同一个**共同内核**：认知水平、知识边界、
误区、思维和表达习惯，这保证了它们的智力与用户相近；每个影子再叠加一个**视角**，也可以叠加场景或时间点，
从而产生不同的思考路径。脚本 `scripts/shadows.py` 用一个 JSON 文件保存内核、影子、模拟记录和保真度反馈，
并为每个影子生成一份**模拟简报**。Agent（或子代理）照着简报扮演影子作答。

## 原则（必须遵守）

1. **不比用户聪明**：影子不能使用内核之外的知识或能力。内核标为「不会」的内容，影子要像用户那样卡住、猜测或绕开；
   active 状态的误区必须保留。Agent 最容易犯的错，是把自己的水平「泄漏」给影子。每个影子作答后都要做 `self_check`。
2. **视角只改关注点，不改深度**：视角决定「先注意到什么、看重什么」，不决定「能想到多深」。
   在当前时间点，能力维度（抽象、严谨、推理深度、元认知、知识）不能调整，脚本会拒绝这类调整；
   只有过去或未来的影子才能在写明假设的前提下改变能力或知识。
3. **证据与不确定性**：内核的每个判断都应有来源。数据不足的方面写进 `gaps`，影子在这些方面要保守，并降低 confidence。
4. **多样性有价值，分歧是重点**：影子之间的视角、场景、时间要真正不同（重复时脚本会警告）。
   综合时必须写出分歧，而不仅仅是共识。
5. **模拟不等于用户本人**：影子的输出只是推测，不能当作用户的真实观点引用，不能用来替用户做决定，
   也不能以用户的身份对外发言。向用户展示时要标明这是模拟。
6. **用真实反馈校准**：拿到用户的真实想法后，用 `run compare` 记录各影子的保真度。持续偏低的影子要修正或退役；
   如果发现内核本身有偏差，就更新内核。

## 运行方式

```bash
SH="uv run <本SKILL.md所在目录>/scripts/shadows.py"     # 零依赖；也可以直接用 python3 运行
$SH --help
$SH lenses                                            # 内置的视角、场景和时间模式
```

- 状态文件是 `$SHADOW_USERS_HOME/<profile>.json`，默认路径为 `~/.local/share/shadow-users/default.json`。
  每个被模拟的用户用一个 profile（`-p 名字`）。
- 结构化输入用 heredoc 通过 stdin 传入 JSON（写 `<<'EOF'`，加引号）。加 `--json` 可以得到结构化输出。

## 流程

### 1. 建立共同内核

先收集画像，来源可以是：用户自述、本次对话中观察到的表现、用户提供的任何画像文件（JSON、Markdown 等，当作普通数据读取，不要求特定格式）。
如果信息明显不足，先问用户几个关键问题（背景、学到哪里、常犯什么错、怎么表达），**不要凭空编造**。
然后把画像整理成内核，字段说明见 `references/designing-shadows.md`：

```bash
$SH init
$SH core set <<'EOF'
{"name": "小王", "source": "用户自述 + 本次对话中 6 道题的表现",
 "summary": "物理系大二，电磁学基础扎实，推导常跳步",
 "cognition": {"abstraction": 3, "rigor": {"level": 2, "evidence": "多题跳步"}, "working_depth": 3,
               "speed": 4, "metacognition": 2, "notes": "自评把握度普遍偏高"},
 "knowledge": [{"domain": "物理/电磁学", "level": "熟练", "known": ["高斯定理", "安培环路定理"],
                "unknown": ["镜像法", "格林函数"]}],
 "misconceptions": [{"concept": "高斯定理求场条件", "detail": "认为只要取球面就能求场", "status": "active"}],
 "thinking_style": ["先给结论再补推导", "喜欢用例题理解概念"],
 "expression": {"register": "口语化", "length": "简短，2–4 句"},
 "values": ["重视考试成绩"], "goals": ["期末前掌握边值问题"],
 "data_confidence": 0.5, "gaps": ["没观察过面对开放问题的表现"]}
EOF
```

画像有了新证据时，重新运行 `core set --reason "..."`。旧内核会保留在 `core_history` 中；已经开始的模拟仍然使用开始时的内核快照。

### 2. 设计影子

根据**要回答的问题**挑选 2–5 个互补的影子，不要追求数量。选择方法见 `references/designing-shadows.md`：

```bash
$SH create <<'EOF'
[{"name": "较真的我", "lens": {"key": "detail"}, "purpose": "暴露条件和边界上的疏漏"},
 {"name": "考场上的我", "lens": {"key": "pragmatist"},
  "scenario": {"key": "exam", "state": "紧张，只剩 10 分钟"}},
 {"name": "怀疑的我", "lens": {"key": "skeptic"},
  "adjustments": [{"trait": "质疑倾向", "direction": "+", "magnitude": 1, "note": "更常追问前提"}]},
 {"name": "三个月前的我", "lens": {"key": "confused_self"},
  "time": {"mode": "past", "offset": "-3个月"},
  "knowledge_overrides": [{"domain": "物理/电磁学", "change": "还没学安培环路定理", "reason": "课程进度"}]}]
EOF
$SH list
```

- `lens`：用 `key` 选内置视角，或者用 `focus` 自定义（例如 `{"focus": "从历史发展的角度看", "questions": ["这是怎么被发现的？"]}`）。
- `scenario`：用 `key` 选内置场景，或者用 `setting` 自定义，可以再加上 `state`（身心状态）和 `constraints`（限制条件）。
- `time`：present / past / future。past 和 future 要写 `offset`；future 还要写 `assumptions`（假设的成长）。
  只有过去或未来的影子，才能用 `knowledge_overrides` 改变知识。
- `adjustments`：对风格、情绪、关注点的小幅调整（magnitude ≤ 2），不能用来调整当下的能力维度。

### 3. 运行模拟

```bash
$SH run start --brief-dir shadow-briefs <<'EOF'
{"problem": "一个点电荷放在接地导体球外，怎么求球外的电场？",
 "purpose": "预测学生在没学镜像法时的思路和卡点", "shadows": ["sh01", "sh02", "sh03"]}
EOF
```

脚本会为每个影子生成一份简报（写入 `--brief-dir`；不加这个参数时直接打印），内容包括：共同内核、该影子的视角、场景和时间设定、
模拟规则，以及要求的输出 JSON 格式。然后**分别**模拟每个影子：

- **隔离**：每个影子只根据自己的简报作答，不看其他影子的输出。如果环境支持子代理，并且用户同意，
  可以每个影子交给一个独立的子代理（最好的隔离方式）。否则就依次模拟，每个影子作答完立即 `add`，再开始下一个。
- 写入结果：

```bash
$SH run add r001 sh01 <<'EOF'
{"interpretation": "...", "approach": "按真实的思考顺序……", "answer": "用用户的口吻和篇幅",
 "confidence": 0.3, "uncertainties": ["..."], "likely_errors": ["..."], "questions": ["..."],
 "self_check": {"beyond_core": false, "notes": ""}}
EOF
```

- `self_check`：回头检查这次作答是否用到了内核没有的知识（例如镜像法），推理是否比用户更严谨，篇幅是否比用户更长。
  如果有，先改回去再提交。同一个影子重复 `add` 会覆盖之前的结果。

### 4. 综合

```bash
$SH run synthesize r001 <<'EOF'
{"consensus": ["各影子都先想到高斯定理"],
 "divergences": ["怀疑视角能察觉到不对称而卡住；细节视角反而落入『取球面就行』的误区"],
 "blind_spots": ["所有影子都不知道镜像法，没有人想到电势边界条件"],
 "insights": ["用户的瓶颈在于不会从边界条件出发思考"],
 "prediction": {"most_likely": "直接套用高斯定理，得出 E=kq/r²", "confidence": 0.6,
                "alternatives": ["意识到不对称之后卡住，转而问接地是什么意思"]},
 "recommendations": ["先用一个反例打破误区，再引入唯一性定理"]}
EOF
$SH run show r001                       # 对比表 + 各影子详情 + 综合
```

向用户展示时说明：这是基于画像的模拟，并给出各视角的要点、分歧，以及对用户最可能想法的预测和置信度。

### 5. 校准（拿到用户的真实想法之后）

```bash
$SH run compare r001 <<'EOF'
{"actual": "<用户的真实回答，逐字>",
 "fidelity": {"sh01": {"score": 0.4, "notes": "用户没有察觉不对称"}, "sh02": {"score": 0.9}},
 "closest": "sh02", "lessons": ["误区比预想的更顽固，怀疑视角高估了用户的觉察力"]}
EOF
```

- 每个参与模拟的影子都要打分（0–1），评分标准见 `references/simulation.md`。`list` 会显示每个影子的平均保真度；
  对比 3 次以上平均仍低于 0.5 的影子会被标记出来，要用 `update` 修正或用 `retire` 退役。
- 如果 lessons 表明内核本身有偏差（例如高估了元认知），就用 `core set --reason` 更新内核。

### 6. 查看

```bash
$SH runs                     # 模拟记录列表
$SH report -o report.md      # 内核 + 影子（含保真度）+ 最近的模拟
$SH validate
```

## 参考文件

- `references/designing-shadows.md`：内核字段、视角和场景库、如何为不同目的挑选影子组合、示例组合
- `references/simulation.md`：模拟时的防泄漏检查清单、综合方法、保真度评分标准、伦理边界
- `references/schema.md`：状态文件结构和维护约定
