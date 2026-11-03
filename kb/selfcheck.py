from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config as app_config
from . import runtime
from .index_lock import IndexLock


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _check(name: str, status: str, message: str) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message}


def _db_stats() -> tuple[dict[str, Any], str]:
    path = app_config.db_path()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(content_status='error'),0), MAX(updated_at) FROM files"
            ).fetchone()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        return {}, str(exc)
    size = path.stat().st_size if path.exists() else 0
    wal = path.with_name(path.name + "-wal")
    return {
        "files": int(row[0] or 0),
        "errors": int(row[1] or 0),
        "updated_at": str(row[2] or ""),
        "db_bytes": size,
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
    }, ""


def _service_report() -> dict[str, Any]:
    data = runtime.read_runtime()
    pids = [int(pid) for pid in data.get("pids", []) if str(pid).isdigit()]
    alive = [pid for pid in pids if _alive(pid)]
    names: list[str] = []
    for pid in alive:
        command = runtime.process_command(pid)
        for name in ("map", "mcp", "watch", "feishu", "monitor"):
            if f"-m kb {name}" in command:
                names.append(name)
                break
        else:
            names.append(str(pid))
    map_pid = 0
    try:
        map_pid = int((app_config.HOME / "logs" / "map.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        map_pid = 0
    return {
        "alive": alive,
        "supervised": alive,
        "names": names,
        "map_pid": map_pid,
        "map_alive": bool(map_pid and _alive(map_pid)),
    }


def run_selfcheck() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    try:
        app_config.ensure_dirs()
        probe = app_config.HOME / "data" / ".selfcheck"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(_check("数据目录", "ok", str(app_config.HOME)))
    except OSError as exc:
        checks.append(_check("数据目录", "fail", f"不可写：{exc}"))

    index, error = _db_stats()
    if error:
        checks.append(_check("索引库", "fail", error))
    else:
        level = "warn" if index["errors"] else "ok"
        checks.append(_check("索引库", level, f"{index['files']:,} 个文件，错误 {index['errors']}"))
        updated = index.get("updated_at") or ""
        age_hours = 0.0
        if updated:
            try:
                moment = datetime.fromisoformat(updated)
                age_hours = (_now() - moment).total_seconds() / 3600
            except ValueError:
                age_hours = 0.0
        if age_hours > 48:
            checks.append(_check("索引新鲜度", "warn", f"最近一次更新在 {age_hours:.0f} 小时前"))
        elif updated:
            checks.append(_check("索引新鲜度", "ok", f"{age_hours:.1f} 小时前更新"))

    try:
        disk = shutil.disk_usage(app_config.HOME)
        free_gb = disk.free / (1024 ** 3)
        level = "ok" if free_gb >= 10 else ("warn" if free_gb >= 3 else "fail")
        checks.append(_check("磁盘空间", level, f"可用 {free_gb:.1f}GB"))
    except OSError as exc:
        checks.append(_check("磁盘空间", "warn", str(exc)))

    for name in ("Documents", "Desktop", "Downloads"):
        path = Path.home() / name
        if not path.exists():
            continue
        if os.access(path, os.R_OK):
            checks.append(_check(name, "ok", "可读"))
        else:
            checks.append(_check(name, "warn", "不可读，检查完全磁盘访问权限"))

    services = _service_report()
    total = len(services["alive"]) + (1 if services["map_alive"] else 0)
    if total >= 5:
        checks.append(_check("服务", "ok", f"{total} 个在运行"))
    elif total > 0:
        checks.append(_check("服务", "warn", f"只有 {total} 个在运行，建议 kb start"))
    else:
        checks.append(_check("服务", "warn", "没有检测到服务进程"))

    lock = IndexLock(app_config.HOME / "data" / "index.lock")
    if lock.acquire():
        lock.release()
        checks.append(_check("索引锁", "ok", "空闲，可以开始新的索引"))
    else:
        checks.append(_check("索引锁", "ok", f"已有索引任务在运行（PID {lock.holder_pid() or '未知'}）"))

    statuses = {item["status"] for item in checks}
    status = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "ok")
    return {
        "time": _now().isoformat(timespec="seconds"),
        "status": status,
        "checks": checks,
        "index": index,
        "services": services,
    }


def format_report(report: dict[str, Any]) -> str:
    index = report.get("index") or {}
    services = report.get("services") or {}
    parts = [
        f"状态 {report['status'].upper()}",
        f"文件 {int(index.get('files') or 0):,}",
        f"错误 {int(index.get('errors') or 0)}",
        f"服务 {len(services.get('alive') or []) + (1 if services.get('map_alive') else 0)} 个",
    ]
    details = [
        f"{item['name']}: {item['message']}"
        for item in report.get("checks", [])
        if item["status"] != "ok"
    ]
    text = " · ".join(parts)
    return text + ("｜" + "；".join(details) if details else "")


def write_report(report: dict[str, Any]) -> Path:
    app_config.ensure_dirs()
    path = app_config.logs_dir() / "selfcheck.log"
    try:
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.replace(path.with_name("selfcheck.log.1"))
    except OSError:
        pass
    line = json.dumps(report, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def run_monitor(interval: int = 1800) -> int:
    interval = max(60, int(interval))
    print(f"系统自检已启动，每 {interval} 秒一次；日志 logs/selfcheck.log", flush=True)
    try:
        while True:
            started = time.time()
            report = run_selfcheck()
            write_report(report)
            print(f"[{report['time']}] {format_report(report)}", flush=True)
            elapsed = time.time() - started
            time.sleep(max(1.0, interval - elapsed))
    except KeyboardInterrupt:
        print("\n自检已停止。", flush=True)
    return 0
