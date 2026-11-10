from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import config as app_config
from . import setup_wizard

APP_NAME = "personal-kb"


def tray_pid_path() -> Path:
    return app_config.HOME / "data" / "tray.pid"


def _read_pid() -> int | None:
    try:
        return int(tray_pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _write_pid() -> None:
    path = tray_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def _clear_pid() -> None:
    try:
        tray_pid_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _trace(message: str) -> None:
    """把托盘动作写入 logs/tray.log，便于排查点击无响应。"""
    try:
        path = app_config.logs_dir() / "tray.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [tray] {message}\n")
    except OSError:
        pass


def tray_available() -> bool:
    try:
        import rumps  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def run_tray(check: bool = False) -> int:
    if check:
        status = setup_wizard.service_status()
        print(f"托盘依赖 rumps：{'可用' if tray_available() else '未安装'}")
        print(f"服务：{status}")
        return 0 if tray_available() else 1

    if sys.platform != "darwin":
        print("菜单栏托盘目前只支持 macOS。Windows 请使用 start.bat。")
        return 1
    if not tray_available():
        print("缺少 rumps。安装：.venv/bin/python -m pip install -e '.[tray]'")
        return 1

    existing = _read_pid()
    if existing and existing != os.getpid() and _pid_alive(existing):
        print(f"菜单栏已经在运行（PID {existing}）。")
        return 0

    import rumps

    class KBTray(rumps.App):
        def __init__(self) -> None:
            super().__init__(APP_NAME, title="KB ○", quit_button=None)
            self.reindex_proc: subprocess.Popen | None = None
            self.stop_proc: subprocess.Popen | None = None
            self.status_item = rumps.MenuItem("状态：检查中…")
            self.log_menu = rumps.MenuItem("查看日志")
            for name in ("map", "mcp", "watch", "feishu", "tray"):
                self.log_menu.add(rumps.MenuItem(name, callback=self._cb("日志-" + name, self.open_log)))
            self._setup_menu()
            self.timer = rumps.Timer(self.refresh, 3)
            self.timer.start()
            self.refresh(None)
            _trace("菜单栏启动")

        def _cb(self, name: str, method: Any) -> Any:
            """包装回调：先写日志再执行，出错也留痕。"""
            def run(sender: Any) -> None:
                _trace(f"点击：{name}")
                try:
                    method(sender)
                except Exception as exc:  # noqa: BLE001
                    _trace(f"出错：{name}：{type(exc).__name__}: {exc}")
            return run

        def _menu_items(self) -> list[Any]:
            return [
                self.status_item,
                None,
                rumps.MenuItem("打开 3D 脑图", callback=self._cb("打开 3D 脑图", self.open_map)),
                rumps.MenuItem("复制 MCP 地址", callback=self._cb("复制 MCP 地址", self.copy_mcp)),
                rumps.MenuItem("环境与接入检查…", callback=self._cb("环境与接入检查", self.open_setup)),
                rumps.MenuItem("重新索引", callback=self._cb("重新索引", self.reindex)),
                self.log_menu,
                rumps.MenuItem("打开项目目录", callback=self._cb("打开项目目录", self.open_project)),
                None,
                rumps.MenuItem("启动服务", callback=self._cb("启动服务", self.start)),
                rumps.MenuItem("停止服务", callback=self._cb("停止服务", self.stop)),
                rumps.MenuItem("退出（停止服务）", callback=self._cb("退出", self.quit)),
            ]

        def _setup_menu(self) -> None:
            try:
                self.menu.clear()
            except Exception:  # noqa: BLE001
                pass
            self.menu = self._menu_items()
            native_menu = getattr(self.menu, "_menu", None)
            if native_menu is not None:
                try:
                    native_menu.setAutoenablesItems_(False)
                except Exception:  # noqa: BLE001
                    pass
            self._enable_menu()

        def _enable_menu(self) -> None:
            def walk(container: Any) -> None:
                try:
                    values = list(container.values())
                except Exception:  # noqa: BLE001
                    return
                for item in values:
                    native = getattr(item, "_menuitem", None)
                    if native is not None:
                        try:
                            native.setEnabled_(True)
                        except Exception:  # noqa: BLE001
                            pass
                    if hasattr(item, "values"):
                        walk(item)

            walk(self.menu)

        def refresh(self, _sender: Any) -> None:
            try:
                self._refresh()
            except Exception as exc:  # noqa: BLE001
                _trace(f"刷新出错：{type(exc).__name__}: {exc}")

        def _refresh(self) -> None:
            if self.stop_proc is not None:
                code = self.stop_proc.poll()
                if code is None:
                    self.title = "KB ◌"
                    self.status_item.title = "正在停止…"
                    self._enable_menu()
                    return
                self.stop_proc = None
                if code != 0:
                    self.status_item.title = "停止失败，查看 logs/start.log"
                self._setup_menu()
            status = setup_wizard.service_status()
            running = bool(status["running"])
            self.title = "KB ●" if running else "KB ○"
            self.status_item.title = "运行中" if running else "已停止"
            if self.reindex_proc is not None:
                code = self.reindex_proc.poll()
                if code is not None:
                    self.reindex_proc = None
                    self.status_item.title = "运行中" if running else "已停止"
                    rumps.alert(APP_NAME, "重新索引完成。" if code == 0 else "重新索引失败，查看 logs/。")
            self._enable_menu()

        def open_map(self, _sender: Any) -> None:
            from . import runtime

            port = 8770
            if not runtime.port_in_use("127.0.0.1", port):
                try:
                    subprocess.Popen(
                        [sys.executable, "-m", "kb", "map", "--port", str(port)],
                        cwd=str(app_config.HOME),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    time.sleep(1.5)
                except OSError as exc:
                    rumps.alert(APP_NAME, f"3D 脑图启动失败：{exc}")
                    return
            subprocess.run(["open", f"http://127.0.0.1:{port}/"], check=False)

        def copy_mcp(self, _sender: Any) -> None:
            port = int(app_config.load_config()["mcp"].get("port", 8766))
            url = f"http://127.0.0.1:{port}/mcp"
            try:
                subprocess.run(["pbcopy"], input=url, text=True, check=False)
                rumps.alert(APP_NAME, f"已复制：{url}")
            except OSError:
                rumps.alert(APP_NAME, url)

        def open_setup(self, _sender: Any) -> None:
            app_config.ensure_dirs()
            script = app_config.logs_dir() / "setup.command"
            script.write_text(
                "#!/bin/bash\n"
                f"cd {shlex.quote(str(app_config.HOME))}\n"
                f"{shlex.quote(sys.executable)} -m kb setup\n"
                "echo\n"
                "read -n 1 -s -r -p \"按任意键关闭...\"\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            subprocess.run(["open", "-a", "Terminal", str(script)], check=False)

        def reindex(self, _sender: Any) -> None:
            if self.reindex_proc is not None and self.reindex_proc.poll() is None:
                rumps.alert(APP_NAME, "已经在重新索引，请稍候。")
                return
            if not rumps.alert(APP_NAME, "现在重新索引全部范围？", ok="开始", cancel="取消"):
                return
            self.reindex_proc = subprocess.Popen(
                [sys.executable, "-m", "kb", "index", "--quiet"],
                cwd=str(app_config.HOME),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.status_item.title = "正在重新索引…"

        def open_log(self, sender: Any) -> None:
            path = app_config.logs_dir() / f"{sender.title}.log"
            if path.exists():
                subprocess.run(["open", str(path)], check=False)
            else:
                rumps.alert(APP_NAME, f"还没有 {sender.title} 日志。")

        def open_project(self, _sender: Any) -> None:
            subprocess.run(["open", str(app_config.HOME)], check=False)

        def start(self, _sender: Any) -> None:
            if setup_wizard.service_status()["running"]:
                rumps.alert(APP_NAME, "服务已经在运行。")
                return
            ok = setup_wizard.start_services()
            rumps.alert(APP_NAME, "服务已启动。" if ok else "启动失败，查看 logs/start.log。")
            self.refresh(None)

        def stop(self, _sender: Any) -> None:
            if self.stop_proc is not None and self.stop_proc.poll() is None:
                rumps.alert(APP_NAME, "正在停止服务，请稍候。")
                return
            if not rumps.alert(
                APP_NAME, "停止网页、MCP、监听和飞书机器人？", ok="停止", cancel="取消"
            ):
                return
            try:
                self.stop_proc = subprocess.Popen(
                    [sys.executable, "-m", "kb", "stop"],
                    cwd=str(app_config.HOME),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.status_item.title = "正在停止…"
                self.title = "KB ◌"
            except OSError as exc:
                rumps.alert(APP_NAME, f"停止失败：{exc}")

        def quit(self, _sender: Any) -> None:
            if not rumps.alert(
                APP_NAME, "退出菜单栏并停止所有服务？", ok="退出", cancel="取消"
            ):
                return
            subprocess.run(
                [sys.executable, "-m", "kb", "stop"],
                cwd=str(app_config.HOME),
                capture_output=True,
            )
            _clear_pid()
            rumps.quit_application()

    _write_pid()
    try:
        KBTray().run()
    finally:
        _clear_pid()
    return 0
