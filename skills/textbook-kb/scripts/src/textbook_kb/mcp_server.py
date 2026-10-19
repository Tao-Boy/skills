"""MCP (stdio) server exposing the read-only query tools.

Register with Claude Code, e.g.:
  claude mcp add textbook-kb -- uv run --project <skill>/scripts --extra mcp tkb mcp
"""

from __future__ import annotations

from typing import Optional

from . import db as D
from . import fmt
from . import search as S

INSTRUCTIONS = """数学/物理教材知识库（MinerU 解析后无损入库）。
检索策略：
1. 不确定书目时先 list_books；需要结构时 get_toc。
2. 有编号的对象（定理2.3、例1.2、式(2.15)、图3-1、习题4.5、§2.3）优先用 lookup，精确且快。
3. 概念/关键词用 search（中文自动分词；LaTeX 或带引号的短语走精确子串匹配），可用 type 过滤
   definition/theorem/example/exercise/proof 等，用 book+section 限定范围。
4. 命中后用 get_chunk(context=1) 读取上下文，或用 read_section 读整节；回答时注明书名、章节与页码。"""


def run(db_path: Optional[str] = None) -> None:
    try:  # mcp >= 2: FastMCP was renamed to MCPServer
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server
        except ImportError as e:  # pragma: no cover
            raise SystemExit("缺少 mcp 依赖：请用 `uv run --extra mcp tkb mcp` 启动") from e

    mcp = Server("textbook-kb", instructions=INSTRUCTIONS)

    def conn():
        return D.connect(db_path, readonly=True)

    def safe(fn):
        try:
            return fn()
        except (S.NotFound, ValueError, FileNotFoundError) as e:
            return f"错误: {e}"

    @mcp.tool()
    def list_books() -> str:
        """列出已导入的教材（id、书名、学科、分支、层次、章节/块数量）。"""
        return safe(lambda: fmt.books(S.list_books(conn())))

    @mcp.tool()
    def get_toc(book: str, depth: int = 3, section: Optional[str] = None) -> str:
        """查看教材目录。book: 书籍 id 或书名片段；depth: 显示层级；section: 只看某章节的子树（如 "2" 或 "2.3"）。"""
        return safe(lambda: fmt.toc(S.toc(conn(), book, depth, section)))

    @mcp.tool()
    def search(query: str, book: Optional[str] = None, type: Optional[str] = None,
               section: Optional[str] = None, limit: int = 8, mode: str = "auto") -> str:
        """全文检索教材内容，返回 chunk 列表（含书名/章节路径/类型/页码/片段）。

        query: 关键词或短语；用双引号包裹或含 LaTeX（如 \\nabla）时做精确子串匹配。
        book: 限定书籍；type: 块类型 definition/theorem/lemma/corollary/example/exercise/solution/proof/remark/text；
        section: 章节编号（需同时给 book）；mode: auto | bm25 | exact。"""
        return safe(lambda: fmt.search(S.search(conn(), query, books=book, types=type, section=section,
                                                limit=limit, mode=mode)))

    @mcp.tool()
    def get_chunk(chunk_id: int, context: int = 0, raw: bool = False) -> str:
        """读取 chunk 全文。context: 前后各附带 N 个相邻块；raw: 显示未经修正的 MinerU 原文。"""
        return safe(lambda: fmt.chunk(S.get_chunk(conn(), int(chunk_id), context, raw)))

    @mcp.tool()
    def read_section(book: str, section: str, max_chars: int = 20000, from_chunk: Optional[int] = None) -> str:
        """读取整个章节（含子章节）。section: "2.3" / "第2章" / "§2.3" / 标题片段 / "s123"。
        超过 max_chars 时截断并给出 from_chunk 以继续读取。"""
        return safe(lambda: fmt.section(S.read_section(conn(), book, section, max_chars, from_chunk)))

    @mcp.tool()
    def lookup(ref: str, book: Optional[str] = None, chapter: Optional[str] = None, limit: int = 5) -> str:
        """按编号精确定位：定理2.3、定义1.1、例1.2、式(2.15)、图3-1、表2、习题4.5、第2章、§2.3。
        chapter 用于区分各章重复编号（如每章都有“例1”）。"""
        return safe(lambda: fmt.lookup(S.lookup(conn(), ref, book, chapter, limit)))

    @mcp.tool()
    def list_units(book: str, type: Optional[str] = None, section: Optional[str] = None, limit: int = 100) -> str:
        """列出某书/某章节中带标签的单元（定义、定理、例题、习题等），用于浏览或批量提取。"""
        return safe(lambda: fmt.units(S.list_units(conn(), book, type, section, limit)))

    mcp.run()
