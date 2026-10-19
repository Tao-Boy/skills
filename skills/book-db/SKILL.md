---
name: book-db
description: 把 MinerU 解析出的数学/物理教材 Markdown（一本或多本）先由模型校验内容、规划结构并分类，再无损切分导入本地 SQLite 知识库；之后提供检索工具，快速、准确地查找和提取定义、定理、公式、例题、习题和章节原文。当用户要导入、入库、整理 MinerU 解析的教材或书籍 Markdown 时使用；当需要从已入库的教材中查找、引用或提取内容时（例如「定理2.3」「式(1.4)」「高斯定理的证明」「第3章的习题」「哪本书讲了格林函数」）也使用。
---

# 教材知识库（book-db）

整个流程分三段：**模型审阅（校验 / 规划 / 分类）→ 脚本无损入库 → 工具检索**。
脚本负责确定性的工作：解析、给出证据、拟初稿、切分、写库、校验。模型负责判断：哪里识别错了、
标题层级应该怎样、书和章节属于什么类别。模型的决定全部写进 `plan.json`，**源文件不做任何改动**。

## 原则（必须遵守）

1. **无损**：源 `.md` 只读。切分只发生在行边界上，把全部 chunk 的原文拼起来必须与源文件逐字节一致
   （入库时会校验 sha256，`verify` 可以随时复查）。
2. **双层文本**：`content_raw` 保存 MinerU 原文，`content` 是修正层（去掉页码/页眉、应用 `fixes`）。
   检索走修正层，需要原貌时用 `--raw`。
3. **不改写正文**：`fixes` 只用来修正明确的识别错误（OCR 错字、LaTeX 语法），每条都要写 `reason`。
   不要润色、补充或改写内容。拿不准的地方记进 `notes`，不要去改。
4. **证据驱动**：不要通读整本书。结构问题看 `outline.md`，识别质量看 `issues.md` 加上
   `show` / `sample` 抽检。

## 运行方式

脚本是一个 uv 项目，位于本 skill 目录下的 `scripts/`。下文用 `$BOOKDB` 表示：

```bash
BOOKDB="uv run --project <本SKILL.md所在目录>/scripts bookdb"
$BOOKDB --help
```

- 数据库默认是 `$BOOK_DB`，未设置时为 `~/.local/share/book-db/book.db`。每条命令都可以用 `--db` 覆盖。
- 查询命令都支持加 `--json` 输出结构化结果。
- 首次运行时 uv 会自动建好环境，无需手动安装。

## A. 导入流程

按下面的清单逐本推进。处理多本书时每本书一个工作目录，彼此独立。书很多时可以让每个子代理负责一本、并行处理。

```
- [ ] 1 prepare：解析，生成 outline.md / issues.md / plan.json 草案
- [ ] 2 内容校验：处理 issues，抽检识别质量，写 fixes / noise_lines / book.quality
- [ ] 3 结构规划：修正章节层级、标题、编号、role，补漏或删除错误的标题
- [ ] 4 分类：book 元数据 + 章节 topics/summary + 修正单元类型（units）
- [ ] 5 check + preview，直到没有 error；然后把 status 设为 "reviewed"
- [ ] 6 ingest + verify，向用户汇报
```

### 1. prepare

```bash
$BOOKDB prepare <a.md|MinerU输出目录> [更多...] --work bookdb-work
```

- MinerU 的输出目录一般是 `<name>/auto/<name>.md`，旁边有 `images/` 和 `<name>_content_list.json`。
  传目录时会递归收集其中所有 `.md`。程序会自动找到 `content_list.json` 并映射 PDF 页码，没有也能正常工作。
- 每本书会生成 `bookdb-work/<id>/`，里面有：
  - `plan.json`：草案，也是你要编辑的唯一文件
  - `outline.md`：章节树、各节字数和置信度标记
  - `issues.md` / `issues.json`：校验发现的问题
- 对同一个工作目录再次运行 prepare，会保留已经编辑过的 plan.json（加 `--force` 才会重置）。

### 2. 内容校验（模型）

先读 `issues.md`，再按 `references/review-guide.md` 逐类处理。要点：

- 用 `$BOOKDB show <work> -l 120-140` 查看带行号的原文；用 `$BOOKDB sample <work> -n 10`（或加 `--kind math`、`--kind table`）
  随机抽检。至少抽检 10 段正文和 10 个公式。
- **error / warn 逐条过一遍**，info 抽查。只有在确定原意时才写 fix：
  `{"line": 91, "find": "varepsilon_0 r$", "replace": "varepsilon_0 r}$", "reason": "补全 \\frac 的花括号"}`
- 确认 `noise_lines`（页码、页眉、页脚）：误收的要删掉，漏掉的要补上。这些行只会从修正层去掉，原文仍然保留。
- 在 `book.quality` 里用一两句话总结识别质量，例如：「公式识别良好；第5章表格错位较多；页眉已去除」。

### 3. 结构规划（模型）

读 `outline.md`，然后编辑 `plan.json` 中的 `sections`（每节一行；以 `_` 开头的字段只作参考，程序会忽略）：

- MinerU 常把所有标题都标成 `#`，草案已按编号（第X章、1.2、1.2.3、§）重建了层级。重点检查标了 `?`
  （低置信度）和 `↑`（从段落提升为标题）的条目。
- 标了 `!` 的超大章节，多半是漏识别了下级标题。用 `show` 找到这些标题所在的块起始行，补成新的 section。
- 如果某个标题其实是正文、页眉或图注，就删掉这一行。删掉后它会成为上一节的正文，不会丢失任何内容。
- 看一眼 `demoted` 列表（目录条目、伪标题），确认没有误杀；需要恢复时把它加回 `sections`。
- 校正 `title`（保留书上的原样，例如「第二章 静电场」）、`number`（规范编号，例如 "2"、"2.3"、"A.1"）
  和 `role`（front_matter / toc / preface / body / summary / exercises / answers / appendix / references / index / back_matter）。
- 规则：第一节必须从第 1 行开始；`line` 严格递增，并且必须是某个块的起始行；`level` 取 1–6。

### 4. 分类（模型）

用词表见 `references/taxonomy.md`：

- `book`：`id`（简短的 ASCII slug，例如 `guo-electrodynamics-3e`）、`title`、`authors`、`subject`（math / physics）、
  `field`、`level`、`edition`、`tags`，以及一句话的 `description`。
- `sections`：给章（level 1）和节（level 2）填写 `topics`（2–5 个规范术语），给章填写一句话的 `summary`。
  这些会进入检索索引，能提升召回率。
- `units`：修正程序自动识别出错的单元。
  `{"line": 610, "type": "theorem", "label": "定理 2.3（高斯定理）", "number": "2.3"}`；
  如果某处被误判为单元，可以写 `{"line": 88, "type": null}` 取消；写 `"type": "text"` 可以强制在这里切开一个普通块。

### 5. check / preview

```bash
$BOOKDB check <work>                  # 试构建：报告 error / warning / 统计，并做无损校验（不写库）
$BOOKDB preview <work> -s 2.3         # 查看某一节的切分结果（类型、标签、行号范围、开头文字）
$BOOKDB outline <work>                # 按当前 plan 重新生成 outline.md
```

把 error 全部消除，warning 逐条判断。抽查 2–3 个典型章节的 preview，确认定理、证明、例题、习题的边界合理。
如果块太大或太小，可以调整 `chunking` 里的 `target_chars`、`max_chars`。最后在 plan.json 中设置 `"status": "reviewed"`。

### 6. ingest / verify

```bash
$BOOKDB ingest <work> [--replace]     # status 不是 reviewed 时会拒绝导入（可用 --allow-draft 强制）
$BOOKDB verify                        # 从库中重建每本书，与源文件哈希比对
```

向用户汇报：书名和 id、章节数和块数、各类单元的数量、识别质量总结、做了哪些 fix、还有哪些遗留问题。

完整的 plan 字段说明见 `references/plan-format.md`。

## B. 检索

| 需求 | CLI | MCP 工具 |
|---|---|---|
| 有哪些书 | `$BOOKDB books` | `list_books` |
| 目录结构 | `$BOOKDB toc <book> [-d 2] [-s 3]` | `get_toc` |
| 按编号精确定位 | `$BOOKDB lookup 定理2.3 [-b book] [--chapter 2]` | `lookup` |
| 概念或关键词 | `$BOOKDB search 格林函数 [-b book] [-t definition] [-s 3]` | `search` |
| 读取某块及上下文 | `$BOOKDB get c123 -C 1 [--raw]` | `get_chunk` |
| 读取整节 | `$BOOKDB read <book> 2.3 [--from c456]` | `read_section` |
| 列出某章的定理和例题 | `$BOOKDB units <book> -t theorem -s 2` | `list_units` |

检索策略：

1. **有编号就用 lookup**。它支持 `定理2.3`、`定义1.1`、`例1.2`、`式(2.15)` 或 `(2.15)`、`图3-1`、`表2`、`习题4.5`、`第2章`、`§2.3`、`Theorem 2.3`、`Eq. (2.15)`。
   如果各章都有「例1」这类重复编号，用 `--chapter` 区分。
2. **概念检索用 search**。中文按二元组建索引，用 BM25 排序：所有词都命中的结果排在前面，部分命中的结果附在后面，标记为 `(partial)`。
   查询含 LaTeX（如 `\nabla`、`\oint`）或用双引号括起来时，会改走精确子串匹配（标记为 `exact`）。
   可以用 `-t` 按块类型过滤：definition / theorem / lemma / corollary / proposition / example / exercise / solution / proof / remark / text。
   默认排除目录和索引中的命中，需要时加 `--all-roles`。
3. **读取**：命中后用 `get -C 1` 带上前后文。证明块会标注它所证明的定理（`证明对象`）。需要通读时用 `read`，内容超长时按提示用 `--from` 翻页。
4. **图片**：输出中的 `[图片]` 是本地文件路径，需要时可以直接读取。
5. **引用格式**：回答时注明「《书名》章节路径，p.页码」。页码是 PDF 页序号，可能与书上印的页码有偏移。
   直接引用公式或原文时以修正层为准；如果有疑问，用 `--raw` 对照 MinerU 原文。

需要更复杂的查询（统计、跨表 join）时，可以直接用 SQL 查询数据库，表结构见 `references/schema.md`。

## C. 注册为 MCP 服务器（可选）

```bash
claude mcp add book-db -- uv run --project <本skill目录>/scripts --extra mcp bookdb mcp
# 指定数据库：claude mcp add book-db -e BOOK_DB=/path/to/book.db -- uv run ...
```

服务器只读，提供上表中的 7 个工具，兼容 mcp 1.x（FastMCP）和 2.x（MCPServer）。

## 参考文件

- `references/plan-format.md`：plan.json 全部字段与校验规则
- `references/review-guide.md`：MinerU 常见问题、各类 issue 的处理方法、审阅清单
- `references/taxonomy.md`：subject / field / level 词表，chunk 类型与章节 role 的定义
- `references/schema.md`：数据库表结构、索引与 SQL 示例
