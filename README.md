# skills

个人 Agent Skills 合集。每个 skill 是 `skills/` 下的一个独立目录（入口为 `SKILL.md`），skill 之间互不依赖，可以单独复制使用。

| Skill | 说明 |
|---|---|
| [book-db](skills/book-db/SKILL.md) | 把 MinerU 解析的数学/物理教材 Markdown 经模型校验、规划、分类后无损导入 SQLite，并提供检索工具（CLI + MCP） |
| [user-status](skills/user-status/SKILL.md) | 逐字记录用户对 Agent 提问的回答，按评分标准评估答案质量，用 JSON 持续维护用户画像 |

## 使用

在 Claude Code 中，把 skill 目录复制或软链接到 `~/.claude/skills/`（个人级）或项目的 `.claude/skills/`（项目级）：

```bash
ln -s "$PWD/skills/book-db" ~/.claude/skills/book-db
```
