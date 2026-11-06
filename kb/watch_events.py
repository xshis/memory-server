from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from . import config as app_config
from .db import connect, delete_under_path, init_schema, list_roots, root_for_path
from .extract import is_excluded, is_sensitive
from .indexer import index_single_file, index_tree

IGNORE_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
IGNORE_SUFFIXES = (".log", ".tmp", ".temp", ".part", ".crdownload", ".download")
IGNORE_DIRS = {
    ".git", ".svn", ".hg", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".idea", ".vscode", ".Trash",
    "tmp", "temp", "cache", "caches", "logs", "log", "node-compile-cache",
    ".npm", ".cache", ".ollama", ".kimi-code", ".workbuddy", ".codex",
    ".codex-deepseek", ".claude", ".agents", ".cursor", ".trae",
    ".vscode-server", "dist", "build",
}


def watchdog_available() -> bool:
    try:
        import watchdog  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _blocked_paths() -> list[Path]:
    home = app_config.HOME
    result = []
    for name in ("data", "logs", ".venv"):
        try:
            result.append((home / name).resolve())
        except OSError:
            result.append(home / name)
    return result


def _ignore(path: Path, config: dict[str, Any], blocked: list[Path] | None = None) -> bool:
    name = path.name.lower()
    if name in IGNORE_NAMES or name.endswith(IGNORE_SUFFIXES):
        return True
    if name.endswith(("-wal", "-shm")):
        return True
    try:
        resolved = path.resolve()
    except OSError:
        return True
    for base in (blocked if blocked is not None else _blocked_paths()):
        try:
            base = Path(base).expanduser().resolve()
        except OSError:
            base = Path(base)
        try:
            resolved.relative_to(base)
            return True
        except ValueError:
            continue
    exclude_dirs = {str(item).lower() for item in config["index"].get("exclude_dirs", [])}
    exclude_dirs |= IGNORE_DIRS
    if {part.lower() for part in resolved.parts} & exclude_dirs:
        return True
    if is_sensitive(resolved):
        return True
    return is_excluded(resolved, list(config["index"].get("exclude_globs", [])))


class _ChangeBuffer:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.lock = threading.Lock()
        self.pending: set[str] = set()
        self.dirty = threading.Event()
        self.stopping = threading.Event()
        self.blocked = _blocked_paths()

    def add(self, path: str) -> None:
        if not path:
            return
        with self.lock:
            self.pending.add(str(path))
        self.dirty.set()

    def stop(self) -> None:
        self.stopping.set()

    def run(self) -> None:
        while not self.stopping.is_set():
            if not self.dirty.wait(0.5):
                continue
            time.sleep(0.8)
            with self.lock:
                batch = list(self.pending)
                self.pending.clear()
                self.dirty.clear()
            self.apply(batch)

    def apply(self, batch: list[str]) -> None:
        if not batch:
            return
        config = app_config.load_config()
        conn = connect(self.db_path)
        init_schema(conn)
        updated = removed = errors = 0
        try:
            for raw in batch[:2000]:
                path = Path(raw)
                try:
                    if _ignore(path, config, self.blocked):
                        continue
                    resolved = path.resolve()
                    if resolved.is_symlink():
                        continue
                    if resolved.exists():
                        if root_for_path(conn, resolved) is None:
                            continue
                        if resolved.is_dir():
                            updated += index_tree(conn, resolved, extra_exclude_dirs=IGNORE_DIRS)
                        elif resolved.is_file():
                            if index_single_file(conn, resolved):
                                updated += 1
                    else:
                        if root_for_path(conn, resolved) is None:
                            continue
                        removed += delete_under_path(conn, resolved)
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    try:
                        conn.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    print(f"[错误] {path}: {exc}", flush=True)
            conn.commit()
        finally:
            conn.close()
        if updated or removed or errors:
            print(f"[{time.strftime('%H:%M:%S')}] 增量：入库 {updated}，删除 {removed}，错误 {errors}",
                  flush=True)
            from .auto_embed import maybe_auto_embed
            maybe_auto_embed(app_config.HOME, reason="watch-events")


def run_event_watch() -> int:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print("缺少 watchdog，先运行：.venv/bin/pip install 'watchdog>=4.0'", flush=True)
        return 2

    app_config.load_config()
    conn = connect(app_config.db_path())
    init_schema(conn)
    roots = list_roots(conn)
    conn.close()
    if not roots:
        print("没有配置索引范围。先运行 `kb scope`。", flush=True)
        return 1

    buffer = _ChangeBuffer(app_config.db_path())

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event):  # noqa: ANN001
            if event.event_type in {"opened", "closed", "closed_no_write"}:
                return
            if event.is_directory:
                if event.event_type == "deleted":
                    buffer.add(event.src_path)
                elif event.event_type == "created":
                    buffer.add(event.src_path)
                elif event.event_type == "moved":
                    buffer.add(event.src_path)
                    buffer.add(event.dest_path)
                return
            if event.event_type == "moved":
                buffer.add(event.src_path)
                buffer.add(event.dest_path)
                return
            buffer.add(event.src_path)

    observer = Observer()
    watched = 0
    for root in roots:
        path = Path(str(root["path"]))
        if path.is_dir():
            observer.schedule(_Handler(), str(path), recursive=True)
            watched += 1
    if not watched:
        print("索引范围都不存在，无法监听。", flush=True)
        return 1
    observer.start()
    worker = threading.Thread(target=buffer.run, daemon=True)
    worker.start()
    print(f"文件事件监听中：{watched} 个范围，只索引变动的文件。按 Ctrl+C 停止。", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已停止监听。", flush=True)
    finally:
        observer.stop()
        observer.join(timeout=5)
        buffer.stop()
        worker.join(timeout=5)
    return 0
