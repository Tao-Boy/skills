# plan.json 格式

`tkb prepare` 会生成一份草案，模型审阅时编辑的就是这个文件。所有字段都**只引用源文件行号**，
文件里不会出现要插入书中的新正文。唯一的例外是 `fixes` 中的替换文本，它只作用于修正层。

列表按一项一行排版，便于用 Edit 工具做小范围修改。以 `_` 开头的键（`_chars`、`_conf`、`_promoted`）
是给审阅者参考的，程序会忽略它们，删改都可以。

```jsonc
{
  "version": 1,
  "source": {                      // 由 prepare 写入，不要修改
    "path": "/abs/path/book.md",
    "sha256": "…",                 // 源文件被改动后，check / ingest 会报错
    "lines": 12345,
    "content_list": "/abs/path/book_content_list.json"   // 用于页码映射，可为 null
  },
  "book": {
    "id": "guo-electrodynamics-3e", // 库内主键；建议用简短的 ASCII slug
    "title": "电动力学（第三版）",
    "authors": ["郭硕鸿"],
    "subject": "physics",           // math | physics | other（见 taxonomy.md）
    "field": "电动力学",
    "level": "undergraduate",
    "language": "zh",
    "edition": "3", "volume": "", "publisher": "高等教育出版社", "year": 2008,
    "tags": ["经典场论"],
    "description": "一句话介绍",
    "quality": "一两句话的识别质量总结"
  },
  "chunking": {"target_chars": 1200, "max_chars": 3000, "statement_chars": 800},
  "sections": [
    {"line": 1,   "level": 1, "number": "",    "title": "[前置内容]",   "role": "front_matter", "topics": [], "summary": ""},
    {"line": 120, "level": 1, "number": "1",   "title": "第一章 电磁现象的普遍规律", "role": "body",
     "topics": ["麦克斯韦方程组", "洛伦兹力"], "summary": "从实验定律归纳出真空和介质中的麦克斯韦方程组。"},
    {"line": 135, "level": 2, "number": "1.1", "title": "1.1 电荷和电场", "role": "body", "topics": ["库仑定律", "高斯定理"], "summary": ""}
  ],
  "units": [
    {"line": 610, "type": "theorem", "label": "定理 2.3（唯一性定理）", "number": "2.3", "title": "唯一性定理"},
    {"line": 702, "type": null}
  ],
  "fixes": [
    {"line": 91, "find": "varepsilon_0 r$", "replace": "varepsilon_0 r}$", "reason": "补全 \\frac 花括号"},
    {"line": 300, "end_line": 302, "find": "是\n\n求解", "replace": "是求解", "reason": "跨页断段合并"}
  ],
  "noise_lines": [35, 61, 85],
  "demoted": [{"line": 5, "text": "# 第一章 …", "reason": "位于目录中"}],
  "notes": "审阅备注（遗留问题、不确定之处）",
  "status": "draft"               // 审阅完成后改为 "reviewed"，ingest 只接受 reviewed
}
```

## sections

每一项表示从 `line` 开始、到下一项 `line - 1` 结束的一个章节（最后一项到文件末尾）。
因此章节一定连续且完整地覆盖全书，不会出现遗漏或重叠。

| 字段 | 规则 |
|---|---|
| `line` | 必填。第一项必须为 1；严格递增；必须是**块起始行**（标题行或段落、公式、表格的首行）。不确定时用 `tkb show` 确认 |
| `level` | 必填，1–6。层级跳跃（例如 1→3）会产生 warning |
| `number` | 规范化编号："2"、"2.3"、"A"、"A.1"。`lookup 第2章` 和 `read <book> 2.3` 靠它定位。没有编号就留空 |
| `title` | 显示标题，保留书中原样（含编号），会拼接成 `path`（例如「第一章 … > 1.1 …」） |
| `role` | 见 taxonomy.md。它决定默认的 chunk 类型（exercises→exercise、answers→solution 等），以及检索时是否默认排除（toc、index） |
| `topics` | 规范术语列表，会进入检索索引的 heading 字段 |
| `summary` | 一句话概括，供 toc 和 read 展示 |

- 补一个漏识别的标题：新增一项，`line` 指向该标题所在段落的首行（MinerU 有时把标题识别成普通段落）。
- 删掉一个错误的标题：删除对应项即可，它会自动并入上一节的正文。
- 一个 section 的起始行不一定是 markdown 标题行，任何块起始行都可以。

## units

用于覆盖程序对**块起始处单元**的自动识别。`line` 必须是块起始行。

- `type` 取下列值之一：definition, theorem, lemma, corollary, proposition, axiom, law, example, exercise, solution, proof,
  remark, algorithm, text, summary。
- `type: null` 表示这个块不是单元起点（例如误识别出的「注：」），它会并入前一个块。
- `type: "text"` 表示在此处强制切开，新开一个普通文本块。
- `number` 会规范化后写入标签表，供 `lookup` 使用；`label` 用于显示。

自动识别的规则：段首出现「定义/定理/引理/推论/命题/公理/定律/例/例题/习题/注/算法」并带编号（或带冒号、加粗），
或段首为「证明：」「解」等。在 exercises 角色的章节中，段首的 `1.`、`2、` 这类编号视为习题；
如果书中只按章内序号编号，标签键会写成「章号.题号」，例如 `lookup 习题3.5`。

## fixes

只作用于修正层（`content`），原文（`content_raw`）不受影响。

| 字段 | 说明 |
|---|---|
| `line` / `end_line` | 作用的行范围（`end_line` 默认等于 `line`）。处理跨多行时，范围内各行会先拼起来再替换，结果放在 `line` 上 |
| `find` | 必须是原文在该行范围内**唯一**出现的片段。出现多次时，要么加长上下文，要么设置 `"all": true` |
| `replace` | 替换后的文本，可以为空字符串（等于删除） |
| `reason` | 必填习惯：说明为什么这是识别错误 |

`find` 要在原文中能找到；同一范围内有多条 fix 时，按列表顺序依次叠加应用。

## noise_lines

页码、页眉、页脚这类单行段落的行号。它们只从修正层中去掉，并且不会影响切块边界。
草案会自动收集纯数字行，以及重复出现 5 次以上的短行。

## chunking

| 参数 | 含义 |
|---|---|
| `target_chars` | 普通文本块的目标大小。达到后，在下一个「安全边界」处切开 |
| `max_chars` | 例题、证明、习题等长单元的软上限。超过后续接一个「（续）」块；超过 2 倍时强制切开 |
| `statement_chars` | 定义、定理这类陈述块中，枚举条件（(1)(2)…）可以延续到的长度 |

安全边界不会出现在以下位置：公式块之前；标题之后；「其中、式中」等续接语之前；以冒号结尾的段落之后；图注、表注之前。

## status

`draft`（草案）或 `reviewed`（已审阅）。只有完成第 2–4 步审阅之后，才把它设为 `reviewed`。
