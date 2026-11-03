from __future__ import annotations

import json
import os
import platform
import plistlib
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import config as app_config
from . import llm, vision
from .db import connect, init_schema, list_roots, status

DOUBAO_APPS = [
    Path("/Applications/DoubaoWork.app"),
    Path.home() / "Applications" / "DoubaoWork.app",
]
DOUBAO_PROFILE = Path.home() / "Library" / "Application Support" / "DoubaoWork"
PRIVACY_PANES = {
    "full_disk": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    "files_folders": "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
}


def path_access(path: Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    try:
        if path.is_dir():
            os.listdir(path)
        else:
            with path.open("rb") as handle:
                handle.read(1)
        return {"path": str(path), "status": "ok"}
    except PermissionError:
        return {"path": str(path), "status": "denied"}
    except OSError as exc:
        return {"path": str(path), "status": "error", "message": str(exc)}


def check_permissions() -> dict[str, Any]:
    home = Path.home()
    checks = [
        path_access(home / "Documents"),
        path_access(home / "Desktop"),
        path_access(home / "Downloads"),
        path_access(app_config.HOME / ".venv" / "pyvenv.cfg"),
    ]
    denied = [item for item in checks if item["status"] in {"denied", "error"}]
    pane = "full_disk"
    if denied and all(Path(item["path"]).name in {"Desktop", "Downloads"} for item in denied):
        pane = "files_folders"
    return {"ok": not denied, "checks": checks, "denied": denied, "pane": pane}


def open_privacy_settings(pane: str = "full_disk") -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"ok": False, "message": "只有 macOS 支持自动打开权限设置"}
    url = PRIVACY_PANES.get(pane, PRIVACY_PANES["full_disk"])
    result = subprocess.run(["open", url], capture_output=True, text=True)
    if result.returncode == 0:
        return {"ok": True, "url": url, "message": "已打开系统设置"}
    fallback = subprocess.run(
        ["open", "-b", "com.apple.systempreferences"],
        capture_output=True, text=True,
    )
    return {
        "ok": fallback.returncode == 0,
        "url": url,
        "message": result.stderr.strip() or fallback.stderr.strip(),
    }


def _doubao_connector_present(indexeddb: Path, needle: bytes, deadline: float = 2.0) -> bool | None:
    if not indexeddb.exists():
        return None
    end = time.time() + deadline
    for path in sorted(indexeddb.rglob("*.log")):
        if time.time() > end:
            return None
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if needle in data:
            return True
    return False


def doubao_status() -> dict[str, Any]:
    app = next((path for path in DOUBAO_APPS if path.exists()), None)
    version = ""
    if app is not None:
        try:
            with (app / "Contents" / "Info.plist").open("rb") as handle:
                info = plistlib.load(handle)
            version = str(info.get("CFBundleShortVersionString", ""))
        except (OSError, plistlib.InvalidFileException):
            version = ""
    connector = None
    if DOUBAO_PROFILE.exists():
        connector = _doubao_connector_present(
            DOUBAO_PROFILE / "Default" / "IndexedDB", b"personal-kb"
        )
    return {
        "installed": app is not None,
        "app": str(app) if app is not None else "",
        "version": version,
        "profile": str(DOUBAO_PROFILE) if DOUBAO_PROFILE.exists() else "",
        "connector": connector,
    }


def _verify_feishu(app_id: str, app_secret: str) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    try:
        request = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            token_data = json.loads(response.read().decode())
        if token_data.get("code") != 0:
            return {"ok": False, "message": f"凭证校验失败：{token_data.get('msg')}"}
        token = token_data.get("tenant_access_token", "")
        request = urllib.request.Request(
            "https://open.feishu.cn/open-apis/bot/v3/info",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            bot = json.loads(response.read().decode())
        if bot.get("code") != 0:
            return {"ok": False, "message": f"机器人能力不可用：{bot.get('msg')}"}
        info = bot.get("bot", {})
        return {
            "ok": True,
            "bot_name": info.get("app_name") or info.get("name") or "",
            "message": "机器人能力正常",
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"ok": False, "message": f"无法联网验证：{exc}"}


def feishu_status(verify: bool = False, create: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "config": str(app_config.feishu_config_path()),
        "configured": False,
        "allow_all": False,
        "open_ids": 0,
        "chat_ids": 0,
        "verified": None,
        "bot_name": "",
        "message": "",
    }
    if not app_config.feishu_config_path().exists() and not create:
        result["message"] = "配置文件还没有创建"
        return result
    try:
        config = app_config.load_feishu_config()
    except RuntimeError as exc:
        result["message"] = str(exc)
        return result
    app_id = str(config.get("app_id", "")).strip()
    app_secret = str(config.get("app_secret", "")).strip()
    result.update({
        "configured": bool(app_id and app_secret),
        "allow_all": bool(config.get("allow_all", False)),
        "open_ids": len(config.get("allowed_open_ids") or []),
        "chat_ids": len(config.get("allowed_chat_ids") or []),
    })
    if verify and result["configured"]:
        info = _verify_feishu(app_id, app_secret)
        result.update({
            "verified": info.get("ok"),
            "bot_name": info.get("bot_name", ""),
            "message": info.get("message", ""),
        })
    return result


def _ps_commands() -> list[str]:
    if os.name == "nt":
        return []
    try:
        output = subprocess.run(
            ["ps", "-axo", "command="], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def service_status() -> dict[str, Any]:
    from . import runtime

    config = (
        app_config.load_config()
        if app_config.config_path().exists()
        else app_config.DEFAULT_CONFIG
    )
    web_port = 8770
    mcp_port = int(config["mcp"].get("port", 8766))
    commands = _ps_commands()
    if not commands:
        data = runtime.read_runtime()
        commands = [runtime.process_command(int(pid)) for pid in data.get("pids", []) or []]

    def running(marker: str) -> bool:
        return any(marker in command for command in commands)

    web = runtime.port_in_use("127.0.0.1", web_port)
    mcp = runtime.port_in_use("127.0.0.1", mcp_port)
    watch = running("kb watch")
    feishu = running("kb feishu")
    monitor = running("kb monitor")
    return {
        "web": web,
        "map": web,
        "mcp": mcp,
        "watch": watch,
        "feishu": feishu,
        "monitor": monitor,
        "running": web or mcp or watch or feishu or monitor,
        "ports": {"web": web_port, "map": web_port, "mcp": mcp_port},
    }


def index_snapshot(create: bool = True) -> dict[str, Any]:
    if not create and not app_config.db_path().exists():
        return {"roots": [], "files": 0, "indexed": 0, "errors": 0, "bytes": 0}
    app_config.ensure_dirs()
    conn = connect(app_config.db_path())
    try:
        init_schema(conn)
        data = status(conn)
        roots = [dict(row) for row in list_roots(conn)]
    finally:
        conn.close()
    return {
        "roots": roots,
        "files": data["files"],
        "indexed": data["indexed"],
        "errors": data["errors"],
        "bytes": data["bytes"],
    }


def start_services() -> bool:
    if service_status()["running"]:
        return True
    app_config.ensure_dirs()
    log_path = app_config.logs_dir() / "start.log"
    with log_path.open("a", encoding="utf-8") as handle:
        subprocess.Popen(
            [sys.executable, "-m", "kb", "start"],
            cwd=str(app_config.HOME),
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.time() + 20
    while time.time() < deadline:
        if service_status()["running"]:
            return True
        time.sleep(0.5)
    return service_status()["running"]


def _ensure_scope() -> bool:
    from .cli import _connection, ensure_scope

    conn = _connection()
    try:
        return ensure_scope(conn)
    finally:
        conn.close()


def _run_index() -> bool:
    from .indexer import index_roots

    conn = connect(app_config.db_path())
    try:
        init_schema(conn)
        roots = list_roots(conn)
        if not roots:
            return False
        stats = index_roots(conn, roots, progress=print)
        print(f"索引完成：{stats.as_dict()}")
        return True
    except KeyboardInterrupt:
        print("\n已中断索引。")
        return False
    finally:
        conn.close()


def _ask(prompt: str, default: bool = True) -> bool:
    if not sys.stdin.isatty():
        return False
    suffix = "Y/n" if default else "y/N"
    answer = input(f"{prompt} ({suffix}) ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "是"}


def _print_permissions(permissions: dict[str, Any]) -> None:
    print("【系统权限】")
    labels = {"ok": "可访问", "denied": "被拒绝", "missing": "不存在", "error": "出错"}
    for item in permissions["checks"]:
        mark = "OK  " if item["status"] == "ok" else "WARN"
        print(f"  [{mark}] {item['path']}：{labels.get(item['status'], item['status'])}")
    if permissions["denied"]:
        print("  处理：系统设置 → 隐私与安全性 → 完全磁盘访问权限 / 文件与文件夹，")
        print("        给终端和 Kimi Code 打开开关，然后完全退出并重开终端。")


def _print_doubao(doubao: dict[str, Any]) -> None:
    print("【豆包工作】")
    if not doubao["installed"]:
        print("  [WARN] 未检测到 /Applications/DoubaoWork.app，请先安装豆包工作电脑版。")
        return
    version = f" {doubao['version']}" if doubao["version"] else ""
    print(f"  [OK  ] 已安装{version}")
    if doubao["connector"] is True:
        print("  [OK  ] 已检测到 personal-kb 连接器")
    elif doubao["connector"] is False:
        print("  [WARN] 还没添加 personal-kb 连接器：")
        print("         豆包工作 → 插件 · 技能 → 管理 → 连接器 → 添加 → 新建自定义连接器")
        print("         名称 personal-kb，类型 HTTP，URL http://127.0.0.1:8766/mcp，Headers 留空")
    else:
        print("  [INFO] 暂时无法读取连接器配置（不影响打开豆包后手动添加）")


def _print_feishu(feishu: dict[str, Any], services: dict[str, Any]) -> None:
    print("【飞书机器人】")
    if not feishu["configured"]:
        print(f"  [WARN] 还没有配置：{feishu['config']}")
        print("         填入 app_id / app_secret，并确认应用已开通机器人、接收消息事件和私聊权限。")
        return
    print(f"  [OK  ] 已配置；白名单用户 {feishu['open_ids']} 人，群 {feishu['chat_ids']} 个"
          + ("（allow_all=true）" if feishu["allow_all"] else ""))
    if feishu["verified"] is True:
        name = f"（{feishu['bot_name']}）" if feishu["bot_name"] else ""
        print(f"  [OK  ] 机器人能力正常{name}")
    elif feishu["verified"] is False:
        print(f"  [WARN] {feishu['message']}")
    print(f"  [{'OK  ' if services['feishu'] else 'WARN'}] 机器人进程："
          f"{'运行中' if services['feishu'] else '未运行'}")


def _print_index(index: dict[str, Any]) -> None:
    print("【索引】")
    if not index["roots"]:
        print("  [WARN] 还没有索引范围。运行 `kb scope` 选择文件夹/盘符，或运行 `kb setup` 交互选择。")
        return
    print(f"  范围 {len(index['roots'])} 个；文件 {index['files']} 个，已提取正文 {index['indexed']} 个，"
          f"错误 {index['errors']} 个")
    for root in index["roots"]:
        print(f"    [{root['id']}] {root['path']}")
    if index["files"] == 0:
        print("  [WARN] 索引为空；启动服务后 watch 会自动建立，也可以运行 `kb index`。")


def _print_services(services: dict[str, Any]) -> None:
    print("【服务】")
    marks = [
        ("3D 脑图", services["web"]),
        ("MCP", services["mcp"]),
        ("监听", services["watch"]),
        ("飞书机器人", services["feishu"]),
    ]
    print("  " + "  ".join(f"{name}:{'运行' if ok else '停止'}" for name, ok in marks))


def _print_vision() -> None:
    print("【图片整理】")
    if vision.available():
        print("  [OK  ] OpenCV 可用（可运行 `kb organize <目录> --strategy image --plan`）")
    else:
        print("  [WARN] 未安装 OpenCV；安装：.venv/bin/python -m pip install -e '.[vision]'")


def _print_ollama(data: dict[str, Any]) -> None:
    print("【本地模型】")
    installed = "已安装" if data["installed"] else "未安装"
    running = "运行中" if data["running"] else "未运行"
    print(f"  Ollama：{installed}；服务：{running}；模式：{data['mode']}；"
          f"配置模型：{data['configured_model']}")
    if data["models"]:
        print("  已安装模型：" + "、".join(data["models"]))
    if data["mode"] == "ollama" and data["configured_model"] not in data["models"]:
        print("  [WARN] 配置的模型还没有下载；运行 `kb model install` 或 `kb setup --with-ollama`。")
    if not data["installed"]:
        print("  安装选项：`kb setup --with-ollama`，或 `kb model install`")


def _install_vision() -> bool:
    print("安装 OpenCV 图片整理依赖…")
    command = [sys.executable, "-m", "pip", "install", "-e", ".[vision]"]
    index = os.environ.get("PIP_INDEX_URL") or os.environ.get("HOMEBREW_PIP_INDEX_URL")
    if index:
        command[4:4] = ["--index-url", index]
    result = subprocess.run(command, cwd=str(app_config.HOME), check=False)
    if result.returncode == 0 and vision.available():
        print("  [OK  ] OpenCV 已安装")
        return True
    print("  [WARN] 安装失败，可稍后手动运行：.venv/bin/python -m pip install -e '.[vision]'")
    return False


def _setup_ollama(model: str) -> bool:
    model = (model or llm.model_name()).strip() or llm.DEFAULT_MODEL
    if not llm.installed():
        print("安装 Ollama…")
        if not llm.install_ollama():
            print("  [WARN] 安装失败。请手动安装：brew install ollama")
            return False
    if not llm.start_server():
        print("  [WARN] Ollama 服务没有启动。可以手动运行：ollama serve")
        return False
    if not llm.model_available(model):
        print(f"下载模型 {model}（大小取决于模型）…")
        if not llm.pull(model):
            print("  [WARN] 模型下载失败。")
            return False
    result = llm.enable(model)
    print(f"  [OK  ] 已启用本地模型：{result['model']}")
    return True


def run_setup(args: Any) -> int:
    check_only = bool(getattr(args, "check", False))
    as_json = bool(getattr(args, "json", False))
    start = bool(getattr(args, "start", False))
    no_open = bool(getattr(args, "no_open", False))

    permissions = check_permissions()
    index = index_snapshot(create=not check_only)
    doubao = doubao_status()
    feishu = feishu_status(verify=not check_only, create=not check_only)
    services = service_status()
    ollama = llm.status()

    report = {
        "platform": platform.platform(),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "paths": {
            "home": str(app_config.HOME),
            "db": str(app_config.db_path()),
            "logs": str(app_config.logs_dir()),
        },
        "permissions": permissions,
        "doubao": doubao,
        "feishu": feishu,
        "vision": {"available": vision.available()},
        "ollama": ollama,
        "index": index,
        "services": services,
    }

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if permissions["ok"] else 1

    print("== 本地知识库助手 · 环境检查 ==")
    print(f"目录：{app_config.HOME}")
    print(f"Python：{platform.python_version()}")
    print()
    _print_permissions(permissions)
    print()
    _print_doubao(doubao)
    print()
    _print_feishu(feishu, services)
    print()
    _print_index(index)
    print()
    _print_services(services)
    print()
    _print_vision()
    print()
    _print_ollama(ollama)
    print()

    if check_only:
        return 0 if permissions["ok"] else 1

    if permissions["denied"] and not no_open and _ask("检测到权限被拒绝，打开系统设置？"):
        result = open_privacy_settings(permissions["pane"])
        print(f"  {result['message']}")
        print("  授权后请完全退出并重开终端/Kimi Code，再运行一次 `kb setup`。")

    if doubao["installed"] and doubao["connector"] is not True and not no_open and _ask("打开豆包工作去添加连接器？"):
        subprocess.run(["open", "-a", "DoubaoWork"], check=False)

    if not feishu["configured"]:
        print(f"  配置文件：{feishu['config']}")
        if not no_open and _ask("现在打开配置文件填写 app_id/app_secret？"):
            subprocess.run(["open", "-e", str(app_config.feishu_config_path())], check=False)

    if not vision.available():
        if getattr(args, "with_vision", False) or (
            not getattr(args, "no_vision", False)
            and _ask("安装 OpenCV 图片整理能力？")
        ):
            if _install_vision():
                print("  之后可用：kb organize <目录> --strategy image --plan")
    model_ready = ollama["running"] and ollama["configured_model"] in ollama["models"]
    if getattr(args, "no_ollama", False):
        llm.disable()
        print("已关闭本地模型问答，恢复摘录式匹配。")
    elif getattr(args, "with_ollama", False) or (
        not (ollama["mode"] == "ollama" and model_ready)
        and _ask("安装/启用本地 Ollama 模型问答？")
    ):
        model = getattr(args, "ollama_model", "") or llm.model_name()
        if _setup_ollama(model):
            ollama = llm.status()
            _print_ollama(ollama)

    if not index["roots"]:
        if not _ensure_scope():
            print("没有选择索引范围，已取消。")
            return 1
        index = index_snapshot()
        _print_index(index)

    if index["files"] == 0 and not start and _ask("现在建立索引？（首次可能较慢）"):
        _run_index()
        index = index_snapshot()

    want_start = start
    if not want_start and _ask("现在启动服务？"):
        want_start = True
    if want_start:
        print("启动服务…")
        if start_services():
            print("  [OK  ] 服务已启动：http://127.0.0.1:8770/")
            feishu = feishu_status(verify=False)
            _print_feishu(feishu, service_status())
        else:
            print("  [WARN] 启动没有完成，查看 logs/start.log")
            return 1

    print()
    print("下一步：")
    print("  3D 脑图：http://127.0.0.1:8770/")
    print("  MCP：http://127.0.0.1:8766/mcp")
    print("  菜单栏：kb tray     查看日志：kb logs")
    return 0
