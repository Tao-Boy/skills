# textbook-kb (`tkb`)

把 MinerU 解析出的数学/物理教材 Markdown **无损**导入 SQLite，并为 agent 提供检索工具（CLI + MCP）。
由 uv 管理，零运行时依赖（MCP 服务器为可选 extra）。

```bash
cd skills/textbook-kb/scripts
uv run tkb --help                      # 首次运行会自动创建 .venv
uv run tkb prepare book.md --work tkb-work
uv run tkb check tkb-work/book
uv run tkb ingest tkb-work/book
uv run tkb search "高斯定理" -k 5
uv run --extra mcp tkb mcp             # stdio MCP 服务器
uv run --group dev pytest              # 测试
```

在其他目录调用时使用 `uv run --project <本目录> tkb ...`。
数据库默认位于 `$TKB_DB` 或 `~/.local/share/textbook-kb/textbook.db`，可用 `--db` 覆盖。

完整工作流见上级目录的 `SKILL.md`，plan 格式见 `../references/plan-format.md`。
