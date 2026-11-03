from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import HOME, db_path, feishu_config_path, load_config, load_feishu_config
from .db import connect, init_schema, now_iso, root_for_path, status
from .organize import apply_plan, plan_organize, undo_plan
from .search import ask, search, summarize_folder

MAX_REPLY = 4000
HELP_TEXT = """本地知识库助手
可用指令：
  找 <关键词>       搜索文件
  发我 <关键词>     搜索并发送第一个匹配文件
  总结 <文件夹>     总结目录
  整理 <文件夹>     生成整理计划（不会立刻移动）
  整理图片 <文件夹> 按图片内容归类（人物/风景/文档截图/其他）
  确认整理 <计划ID> 执行整理
  撤销 <计划ID>     撤销整理
  状态              查看索引状态
  /whoami           查看自己的 open_id / chat_id
  帮助              显示本说明
也可以直接问问题，例如：报销流程是什么？"""


def strip_mentions(text: str) -> str:
    text = re.sub(r"@_user_\d+", "", text)
    text = re.sub(r"@_all", "", text)
    return text.strip()


def _model_enabled() -> bool:
    try:
        return str(load_config().get("models", {}).get("mode", "none")).lower() == "ollama"
    except Exception:  # noqa: BLE001
        return False


def _resolve_file_arg(conn, argument: str) -> Path | None:
    """把“发我 docs/x.md”这类参数解析成索引范围内的真实文件。"""
    raw = argument.strip().strip("\"'")
    if not raw or any(char in raw for char in "*?[]"):
        return None
    candidates = [Path(raw).expanduser()]
    if not candidates[0].is_absolute():
        candidates.extend([HOME / raw, Path.home() / raw])
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file():
            continue
        if root_for_path(conn, resolved) is None:
            continue
        return resolved
    return None


def route_message(text: str) -> tuple[str, str]:
    """Turn a Feishu message into (action, argument)."""
    original = strip_mentions(text)
    if not original:
        return "ignore", ""
    if original in {"/help", "/帮助", "帮助", "help"}:
        return "help", ""
    if original in {"/status", "/状态", "状态"}:
        return "status", ""

    match = re.match(r"^/?(?:确认整理|确认归类)\s+([0-9a-zA-Z_-]+)", original)
    if match:
        return "apply", match.group(1)
    match = re.match(r"^/?(?:撤销整理|撤销归类|撤销)\s+([0-9a-zA-Z_-]+)", original)
    if match:
        return "undo", match.group(1)
    match = re.match(r"^/?(?:整理图片|图片整理)(?:一下)?\s+(.+)$", original)
    if match:
        return "plan_image", match.group(1).strip()
    match = re.match(r"^/?(?:整理|归类)(?:一下)?\s+(.+)$", original)
    if match:
        return "plan", match.group(1).strip()
    match = re.match(r"^/?(?:总结|汇总)(?:一下)?\s+(.+)$", original)
    if match:
        return "summarize", match.group(1).strip()
    match = re.match(
        r"^(?:帮我)?(?:找一下|查找|搜索|找|获取|发我|发一下|发送)\s*(.+)$",
        original,
    )
    if match:
        return "search", match.group(1).strip()
    if "确认整理" in original:
        parts = original.split()
        if parts:
            return "apply", parts[-1]
    return "ask", original


def _truncate(text: str, limit: int = MAX_REPLY) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（内容过长已截断）"


def _format_sources(sources: list[dict[str, Any]], limit: int = 3) -> str:
    lines = []
    for index, source in enumerate(sources[:limit], 1):
        lines.append(f"[{index}] {source.get('name', '')}  {source.get('path', '')}")
    return "\n".join(lines)


def handle_text(conn, text: str) -> dict[str, Any]:
    """Execute a message against the local knowledge base.

    Returns {"text": reply, "file": optional path to send}.
    """
    action, argument = route_message(text)
    if action == "ignore":
        return {"text": ""}
    if action == "help":
        return {"text": HELP_TEXT}
    if action == "status":
        data = status(conn)
        lines = [
            f"文件：{data['files']}，已提取正文：{data['indexed']}，"
            f"过大：{data['too_large']}，不支持：{data['unsupported']}，错误：{data['errors']}",
        ]
        for root in data["roots"]:
            lines.append(f"[{root['id']}] {root['path']}  文件 {root['files']}")
        return {"text": "\n".join(lines)}

    if action == "search":
        wants_file = any(word in text for word in ("发我", "发一下", "发送", "获取"))
        if wants_file:
            candidate = _resolve_file_arg(conn, argument)
            if candidate is not None:
                return {"text": f"发送：{candidate.name}", "file": str(candidate)}
        results = search(conn, argument, limit=8)
        if not results:
            return {"text": f"没有找到与「{argument}」相关的文件。"}
        lines = [f"找到 {len(results)} 个文件："]
        for index, item in enumerate(results, 1):
            lines.append(f"{index}. {item['name']}  [{item['size_text']}]\n   {item['path']}")
        payload: dict[str, Any] = {"text": "\n".join(lines)}
        if wants_file and len(results) == 1:
            payload["file"] = results[0]["path"]
        elif wants_file:
            payload["text"] += "\n\n匹配到多个文件，请回复更精确的关键词。"
        return payload

    if action == "summarize":
        try:
            data = summarize_folder(conn, argument)
        except Exception as exc:  # noqa: BLE001
            return {"text": f"总结失败：{exc}"}
        return {"text": _truncate(data["summary"])}

    if action in {"plan", "plan_image"}:
        strategy = "image" if action == "plan_image" else "type"
        try:
            plan = plan_organize(conn, argument, strategy=strategy)
        except Exception as exc:  # noqa: BLE001
            return {"text": f"生成整理计划失败：{exc}"}
        if not plan["operations"]:
            return {"text": "没有需要整理的文件。"}
        label = "图片整理" if strategy == "image" else "整理"
        lines = [f"{label}计划：{plan['plan_id']}，共 {len(plan['operations'])} 个文件。"]
        target = Path(plan["target"])
        for operation in plan["operations"][:10]:
            source_name = Path(operation["src"]).name
            try:
                destination = str(Path(operation["dst"]).relative_to(target))
            except ValueError:
                destination = operation["dst"]
            lines.append(f"  {source_name} -> {destination}")
        if len(plan["operations"]) > 10:
            lines.append(f"  ... 还有 {len(plan['operations']) - 10} 个")
        lines.append(f"\n确认执行请回复：确认整理 {plan['plan_id']}")
        return {"text": "\n".join(lines)}

    if action == "apply":
        try:
            result = apply_plan(conn, argument, confirm=True)
        except Exception as exc:  # noqa: BLE001
            return {"text": f"执行失败：{exc}"}
        return {"text": f"已执行 {result['applied_count']} 个，失败 {result['failed_count']} 个。"}

    if action == "undo":
        try:
            result = undo_plan(conn, argument)
        except Exception as exc:  # noqa: BLE001
            return {"text": f"撤销失败：{exc}"}
        return {"text": f"已恢复 {result['restored_count']} 个，失败 {result['failed_count']} 个。"}

    data = ask(conn, argument)
    text = data["answer"]
    if data.get("sources"):
        text += "\n\n来源：\n" + _format_sources(data["sources"])
    return {"text": _truncate(text)}


class FeishuBot:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_feishu_config()
        self.app_id = str(self.config.get("app_id", "")).strip()
        self.app_secret = str(self.config.get("app_secret", "")).strip()
        self.allow_all = bool(self.config.get("allow_all", False))
        self.allowed_open_ids = set(self.config.get("allowed_open_ids", []) or [])
        self.allowed_chat_ids = set(self.config.get("allowed_chat_ids", []) or [])
        self.client = None

    def _connection(self):
        conn = connect(db_path())
        init_schema(conn)
        return conn

    def _allowed(self, open_id: str, chat_id: str) -> bool:
        if self.allow_all:
            return True
        return open_id in self.allowed_open_ids or chat_id in self.allowed_chat_ids

    def _seen(self, conn, event_id: str) -> bool:
        if not event_id:
            return False
        cursor = conn.execute(
            "INSERT OR IGNORE INTO feishu_events(event_id, created_at) VALUES(?, ?)",
            (event_id, now_iso()),
        )
        conn.commit()
        return cursor.rowcount == 0

    def _send_text(self, chat_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            print(f"[feishu] 发送失败：{response.code} {response.msg}")

    def _send_file(self, chat_id: str, path: str) -> None:
        from lark_oapi.api.im.v1 import (
            CreateFileRequest,
            CreateFileRequestBody,
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            self._send_text(chat_id, f"文件不存在：{path}")
            return
        if file_path.stat().st_size > 30 * 1024 * 1024:
            self._send_text(chat_id, f"文件超过 30MB，暂不发送：{path}")
            return
        with file_path.open("rb") as handle:
            body = (
                CreateFileRequestBody.builder()
                .file_type("stream")
                .file_name(file_path.name)
                .file(handle)
                .build()
            )
            request = CreateFileRequest.builder().request_body(body).build()
            response = self.client.im.v1.file.create(request)
        if not response.success():
            self._send_text(chat_id, f"文件上传失败：{response.code} {response.msg}")
            return
        file_key = response.data.file_key
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("file")
            .content(json.dumps({"file_key": file_key}))
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        self.client.im.v1.message.create(request)

    def handle_event(self, data: Any) -> None:
        try:
            event = data.event
            message = event.message
            sender = event.sender
            open_id = sender.sender_id.open_id if sender and sender.sender_id else ""
            chat_id = message.chat_id or ""
            chat_type = message.chat_type or ""
            event_id = getattr(data.header, "event_id", "") if data.header else ""
            if sender and sender.sender_type and sender.sender_type != "user":
                return
            if message.message_type and message.message_type != "text":
                return
            if chat_type == "group" and not message.mentions:
                return
            if not self._allowed(open_id, chat_id):
                print(f"[feishu] 忽略未授权用户：{open_id[:8]}… chat={chat_id[:8]}…")
                return
            print(f"[feishu] 收到消息 open_id={open_id[:8]}… chat={chat_id[:8]}…")
            content = json.loads(message.content or "{}")
            text = strip_mentions(str(content.get("text", "")))
            if not text:
                return
            if text.strip().lower() in {"/whoami", "whoami", "我的id", "我的id是"}:
                self._send_text(
                    chat_id,
                    f"open_id: {open_id}\nchat_id: {chat_id}\nchat_type: {chat_type}",
                )
                return
            if _model_enabled() and route_message(text)[0] == "ask":
                self._send_text(chat_id, "思考中，请稍后…")
            conn = self._connection()
            try:
                if self._seen(conn, event_id):
                    return
                result = handle_text(conn, text)
            finally:
                conn.close()
            if result.get("text"):
                self._send_text(chat_id, result["text"])
            if result.get("file"):
                self._send_file(chat_id, result["file"])
        except Exception as exc:  # noqa: BLE001
            print(f"[feishu] 处理消息失败：{exc}")


def run_bot() -> int:
    config = load_feishu_config()
    app_id = str(config.get("app_id", "")).strip()
    app_secret = str(config.get("app_secret", "")).strip()
    if not app_id or not app_secret:
        print("飞书配置为空。")
        print(f"请编辑：{feishu_config_path()}")
        print("填入 app_id、app_secret，并按 README 的飞书接入手册配置权限和长连接事件。")
        return 1
    try:
        import lark_oapi as lark
    except ImportError:
        print("缺少 lark-oapi。先运行：")
        print("  .venv/bin/python -m pip install -e '.[feishu]'")
        return 1

    bot = FeishuBot(config)
    client = (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )
    bot.client = client

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(bot.handle_event)
        .register_p2_im_message_message_read_v1(lambda _data: None)
        .register_p2_im_message_reaction_created_v1(lambda _data: None)
        .register_p2_im_message_reaction_deleted_v1(lambda _data: None)
        .build()
    )
    ws_client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.WARNING,
        auto_reconnect=True,
    )
    print("飞书机器人已启动，等待消息。按 Ctrl+C 停止。")
    while True:
        try:
            ws_client.start()
            print("[feishu] 长连接已结束，5 秒后重连...", flush=True)
        except KeyboardInterrupt:
            print("\n已停止飞书机器人。")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[feishu] 长连接异常：{exc}；5 秒后重连", flush=True)
        time.sleep(5)
