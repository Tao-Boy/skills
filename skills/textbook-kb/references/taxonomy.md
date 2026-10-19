# 分类词表

分类要保持一致，这样跨书检索时的过滤（`--subject`、`--field`）才可靠。词表里没有合适的值时，可以新增，但要沿用相同的命名风格。

## subject

| 值 | 含义 |
|---|---|
| `math` | 数学 |
| `physics` | 物理 |
| `other` | 其他（例如偏数学的理论力学讲义也可以归入 physics，以作者的定位为准） |

## field（中文规范名）

**数学**：数学分析、高等数学、线性代数、高等代数、解析几何、常微分方程、偏微分方程、复变函数、实变函数、
泛函分析、概率论、数理统计、随机过程、抽象代数、数论、组合数学、图论、拓扑学、微分几何、代数几何、
数值分析、最优化、离散数学、数理逻辑、数学物理方法、调和分析、动力系统

**物理**：普通物理、力学、热学、电磁学、光学、原子物理、理论力学、热力学与统计物理、电动力学、量子力学、
高等量子力学、量子场论、固体物理、凝聚态物理、原子分子物理、核物理、粒子物理、广义相对论、宇宙学、天体物理、
等离子体物理、流体力学、连续介质力学、计算物理、数学物理方法、群论（物理）、非线性物理

一本书涵盖多个分支时，`field` 取主要的那个，其余写进 `tags`。

## level

| 值 | 含义 |
|---|---|
| `high_school` | 高中、竞赛入门 |
| `competition` | 数学或物理竞赛 |
| `undergraduate_intro` | 大学基础课（高等数学、普通物理等） |
| `undergraduate` | 本科专业课 |
| `graduate` | 研究生教材 |
| `monograph` | 专著、研究参考书 |
| `problem_book` | 习题集、题解 |

## 章节 role

| role | 含义 | 默认 chunk 类型 | 检索默认 |
|---|---|---|---|
| `front_matter` | 封面、版权页、丛书序等正文之前的内容 | front_matter | 降权 |
| `toc` | 目录 | toc | 排除 |
| `preface` | 前言、序言、出版说明 | text | 正常 |
| `body` | 正文 | text | 正常 |
| `summary` | 本章小结、内容提要 | summary | 正常 |
| `exercises` | 习题、思考题、练习 | exercise | 正常 |
| `answers` | 习题答案、提示 | solution | 正常 |
| `appendix` | 附录 | text | 正常 |
| `references` | 参考文献 | reference | 降权 |
| `index` | 索引 | index | 排除 |
| `back_matter` | 后记等 | text | 降权 |

子章节默认继承父章节的 role，例如习题下的「A 组」「B 组」也是 exercises。

## chunk 类型

| 类型 | 识别依据（段首） | 备注 |
|---|---|---|
| `definition` | 定义 / Definition | 陈述类：陈述结束（遇到下一个普通段落）时切开 |
| `theorem` | 定理 / Theorem | 陈述类 |
| `lemma` | 引理 / Lemma | 陈述类 |
| `corollary` | 推论 / Corollary | 陈述类 |
| `proposition` | 命题 / Proposition | 陈述类 |
| `axiom` | 公理、公设 / Axiom, Postulate | 陈述类 |
| `law` | 定律 / Law | 陈述类（需带编号或冒号） |
| `proof` | 证明 / Proof / 证 | 记录它所证明的陈述（`ref_chunk_id`）；遇到 □ ∎ 证毕 时结束 |
| `example` | 例 / 例题 / Example | 其后的「解」并入同一块 |
| `exercise` | 习题 / 练习 / Exercise / Problem，或 exercises 章节中带编号的条目 | |
| `solution` | 解 / 解答 / Solution（不跟在例题后面时） | |
| `remark` | 注 / 注意 / Remark / Note | |
| `algorithm` | 算法 / Algorithm | |
| `text` | 普通正文 | |
| `summary` / `toc` / `index` / `reference` / `front_matter` | 由所在章节的 role 决定 | |

## 标签（labels）种类

`lookup` 依据下面这些标签做精确定位：

- 各单元类型加编号，例如 `theorem 2.3`、`example 1.2`
- `equation`：取自 `\tag{…}`、公式末尾的 `(2.15)`，或紧跟在公式块后面、独占一行的 `(2.15)`；保存公式的 LaTeX
- `figure` / `table`：取自「图 3-1 …」「表 2.1 …」这类图注、表注

编号会统一规范为点分形式：`2-3`、`2．3`、`二.3` 都写成 `2.3`。
