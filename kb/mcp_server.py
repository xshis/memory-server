from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from .config import load_config
from .db import connect, init_schema, list_roots
from .indexer import index_roots
from .organize import apply_plan, plan_organize, undo_plan
from .search import ask, get_file, list_directory, search, summarize_folder

server = MCPServer(
    "personal-kb",
    instructions=(
        "本地文件知识库。可以搜索文件、读取文件、列目录、总结文件夹、回答问题、整理文件夹。"
        "所有路径必须位于已配置的索引范围内。"
        "整理文件必须先调用 plan_organize 生成计划，确认后再调用 apply_organize(confirm=true)。"
        "不要假设文件存在；先搜索或列目录。"
    ),
)


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _db_path():
    from .config import db_path
    return db_path()


def _connection():
    conn = connect(_db_path())
    init_schema(conn)
    return conn


@server.tool(description="按文件名或内容搜索本地文件，返回路径、大小、匹配片段。")
def search_files(query: str, limit: int = 20, ext: str = "", root: str = "") -> str:
    conn = _connection()
    try:
        return _json(search(conn, query, limit=limit, ext=ext, root=root))
    finally:
        conn.close()


@server.tool(description="读取本地文件或目录的信息；文件会返回文本预览。路径必须在索引范围内。")
def get_file_info(path: str, max_chars: int = 5000) -> str:
    conn = _connection()
    try:
        return _json(get_file(conn, path, max_chars=max_chars))
    finally:
        conn.close()


@server.tool(description="列出目录内容，depth 控制递归深度。路径必须在索引范围内。")
def list_dir(path: str, depth: int = 1, limit: int = 200) -> str:
    conn = _connection()
    try:
        return _json(list_directory(conn, path, depth=depth, limit=limit))
    finally:
        conn.close()


@server.tool(description="总结文件夹：文件数、类型分布、最近修改、内容片段。")
def summarize(path: str, max_files: int = 2000) -> str:
    conn = _connection()
    try:
        return _json(summarize_folder(conn, path, max_files=max_files))
    finally:
        conn.close()


@server.tool(description="基于本地索引回答问题，返回摘录式答案和来源。")
def ask_knowledge(question: str, limit: int = 5) -> str:
    conn = _connection()
    try:
        return _json(ask(conn, question, limit=limit))
    finally:
        conn.close()


@server.tool(description="查看索引状态：根目录、文件数、大小、错误数。")
def index_status() -> str:
    conn = _connection()
    try:
        roots = [dict(row) for row in list_roots(conn)]
        from .db import status
        data = status(conn)
        data["roots"] = roots
        return _json(data)
    finally:
        conn.close()


@server.tool(description="重新索引；root 为空时索引全部范围。full=true 会忽略缓存重新处理。")
def reindex(root: str = "", full: bool = False) -> str:
    conn = _connection()
    try:
        roots = list_roots(conn)
        if root:
            from .db import root_for_path
            selected = root_for_path(conn, root)
            if selected is None:
                return _json({"error": f"索引范围里没有这个路径：{root}"})
            roots = [selected]
        stats = index_roots(conn, roots, full=full, progress=lambda _message: None)
        return _json({"ok": True, **stats.as_dict()})
    finally:
        conn.close()


@server.tool(description="生成整理计划：按类型 type、月份 date 或内容 content 归类；只生成计划，不移动文件。")
def plan_files(path: str, strategy: str = "type", recursive: bool = False,
               rename_pattern: str = "") -> str:
    conn = _connection()
    try:
        return _json(plan_organize(
            conn, path, strategy=strategy, recursive=recursive,
            rename_pattern=rename_pattern,
        ))
    finally:
        conn.close()


@server.tool(description="执行整理计划。必须传 confirm=true；默认不删除、不覆盖，重名自动加后缀。")
def apply_files(plan_id: str, confirm: bool = False) -> str:
    conn = _connection()
    try:
        return _json(apply_plan(conn, plan_id, confirm=confirm))
    finally:
        conn.close()


@server.tool(description="撤销之前执行的整理计划。")
def undo_files(plan_id: str) -> str:
    conn = _connection()
    try:
        return _json(undo_plan(conn, plan_id))
    finally:
        conn.close()


def run_mcp(transport: str = "stdio", host: str | None = None, port: int | None = None) -> None:
    config = load_config()
    if transport == "stdio":
        server.run(transport="stdio")
        return

    from .auto_embed import MIN_START_GAP, maybe_auto_embed
    from .config import HOME as _KB_HOME

    maybe_auto_embed(_KB_HOME, reason="mcp-start", gap_threshold=MIN_START_GAP)

    host = host or str(config["mcp"].get("host", "127.0.0.1"))
    port = port or int(config["mcp"].get("port", 8766))
    web_host = "127.0.0.1"
    web_port = 8770

    import uvicorn
    from starlette.responses import HTMLResponse, JSONResponse
    from starlette.routing import Route

    async def index(_request):
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<title>MCP 接口</title><style>"
            "body{font:16px/1.7 -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;"
            "background:#f5f7fa;color:#172033;display:flex;min-height:100vh;align-items:center;"
            "justify-content:center}.card{background:#fff;border:1px solid #e6e9ef;border-radius:14px;"
            "padding:32px 36px;max-width:560px;box-shadow:0 8px 24px rgba(16,24,40,.06)}"
            "h1{font-size:22px;margin:0 0 10px}code{background:#f2f4f7;border-radius:6px;padding:2px 6px}"
            "a{color:#1f6feb}</style></head><body><div class='card'>"
            "<h1>这是 MCP 接口，不是网页</h1>"
            "<p>浏览器直接打开 MCP 端口会看到这条提示，这是正常的。</p>"
            f"<p>3D 脑图：<a href='http://{web_host}:{web_port}/'>"
            f"http://{web_host}:{web_port}/</a></p>"
            f"<p>MCP 地址（给豆包/机器人）：<code>http://{host}:{port}/mcp</code></p>"
            "<p>MCP 客户端请使用 <code>/mcp</code> 路径并发起 MCP 握手，不要用浏览器直接访问。</p>"
            "</div></body></html>"
        )

    async def health(_request):
        return JSONResponse({"ok": True, "mcp": "/mcp", "web": f"http://{web_host}:{web_port}/"})

    app = server.streamable_http_app(host=host)
    app.router.routes.insert(0, Route("/", index, methods=["GET"]))
    app.router.routes.insert(1, Route("/health", health, methods=["GET"]))
    uvicorn.run(app, host=host, port=port)
