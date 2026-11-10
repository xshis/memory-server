from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config as app_config
from .db import (
    add_root,
    connect,
    init_schema,
    list_roots,
    remove_root,
    status,
)
from .doctor import print_checks, run_doctor
from .indexer import index_roots
from .organize import apply_plan, plan_organize, undo_plan
from .search import ask, format_search_results, get_file, list_directory, search, summarize_folder


def _connection():
    app_config.load_config()
    conn = connect(app_config.db_path())
    init_schema(conn)
    return conn


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{value}B"


def _windows_drives() -> list[Path]:
    drives = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        candidate = Path(f"{letter}:\\")
        try:
            if candidate.exists():
                drives.append(candidate)
        except OSError:
            continue
    return drives


def _detect_candidates() -> list[tuple[str, Path]]:
    home = Path.home()
    candidates: list[tuple[str, Path]] = [("用户目录", home)]
    if os.name == "nt":
        for drive in _windows_drives():
            if drive != Path.home().anchor and str(drive) != str(Path.home().anchor):
                candidates.append((f"盘符 {drive}", drive))
    else:
        volumes = Path("/Volumes")
        if volumes.is_dir():
            for child in sorted(volumes.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    candidates.append((f"外接盘 {child.name}", child))
    return candidates


def _show_roots(conn) -> None:
    roots = list_roots(conn)
    if not roots:
        print("当前没有索引范围。")
        return
    print("当前索引范围：")
    for row in roots:
        print(f"  [{row['id']}] {row['path']}  ({row['label']})")


def _prompt_path() -> Path | None:
    raw = input("输入文件夹或盘符（例如 D:\\资料 或 ~/Documents）：").strip()
    if not raw:
        return None
    if os.name == "nt" and len(raw) == 2 and raw[1] == ":":
        raw = raw + "\\"
    path = Path(raw).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    if not path.exists() or not path.is_dir():
        print(f"路径不存在或不是目录：{path}")
        return None
    return path


def interactive_scope(conn) -> None:
    while True:
        print()
        print("请选择索引范围：")
        for index, (label, path) in enumerate(_detect_candidates(), 1):
            print(f"  {index}) {label}：{path}")
        print("  a) 添加指定文件夹/盘符")
        print("  l) 查看当前范围")
        print("  c) 清空当前范围")
        print("  0) 退出")
        choice = input("输入编号：").strip().lower()
        if choice in {"0", "q", ""}:
            return
        if choice == "l":
            _show_roots(conn)
            continue
        if choice == "c":
            if input("确认清空全部索引范围？(y/N) ").strip().lower() == "y":
                for row in list_roots(conn):
                    remove_root(conn, str(row["id"]))
                print("已清空。")
            continue
        if choice == "a":
            path = _prompt_path()
            if path is not None:
                add_root(conn, path)
                print(f"已添加：{path}")
            continue
        if choice.isdigit():
            candidates = _detect_candidates()
            index = int(choice) - 1
            if 0 <= index < len(candidates):
                path = candidates[index][1]
                add_root(conn, path)
                print(f"已添加：{path}")
                continue
        print("无效输入。")


def ensure_scope(conn) -> bool:
    """If no roots are configured, prompt once before starting a service."""
    if list_roots(conn):
        return True
    if not sys.stdin.isatty():
        print("还没有配置索引范围。")
        print("请在终端运行一次 `kb scope` 选择文件夹或盘符，或先运行 `kb init --root <路径>`。")
        return False
    print("还没有配置索引范围，先选择要索引的文件夹/盘符。")
    interactive_scope(conn)
    if not list_roots(conn):
        print("没有选择索引范围，已取消启动。")
        return False
    return True


def cmd_init(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        for root in args.root or []:
            add_root(conn, root)
        print(f"数据目录：{app_config.HOME}")
        print(f"数据库：{app_config.db_path()}")
        _show_roots(conn)
    finally:
        conn.close()
    if args.doctor:
        return print_checks(run_doctor(include_feishu=args.feishu, include_ollama=args.ollama))
    return 0


def cmd_scope(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        if args.clear:
            for row in list_roots(conn):
                remove_root(conn, str(row["id"]))
            print("已清空索引范围。")
        for root in args.add or []:
            add_root(conn, root)
            print(f"已添加：{Path(root).expanduser().resolve()}")
        for root in args.remove or []:
            removed = remove_root(conn, root)
            print(f"已移除：{root}" if removed else f"未找到：{root}")
        if args.home:
            add_root(conn, Path.home())
        if args.all and os.name == "nt":
            for drive in _windows_drives():
                add_root(conn, drive)
        _show_roots(conn)
        if args.interactive or (not any([args.add, args.remove, args.clear, args.home, args.all, args.list])):
            interactive_scope(conn)
    finally:
        conn.close()
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from .index_lock import IndexLock

    lock = IndexLock(app_config.HOME / "data" / "index.lock")
    if not lock.acquire():
        pid = lock.holder_pid()
        print(f"已有 kb index 在运行（PID {pid or '未知'}），本次跳过。", file=sys.stderr)
        return 1
    conn = _connection()
    try:
        roots = list_roots(conn)
        if not roots and not args.root:
            if not ensure_scope(conn):
                return 1
            roots = list_roots(conn)
        if args.root:
            from .db import root_for_path
            selected = root_for_path(conn, args.root)
            if selected is None:
                print(f"索引范围里没有这个路径：{args.root}")
                return 1
            roots = [selected]
        if not roots:
            return 1
        progress = None if args.quiet else print
        stats = index_roots(conn, roots, full=args.full, progress=progress)
        from .auto_embed import maybe_auto_embed
        maybe_auto_embed(app_config.HOME, reason="index")
        if args.json:
            _print_json(stats.as_dict())
        return 0
    finally:
        conn.close()
        lock.release()


def cmd_search(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        results = search(conn, args.query, limit=args.limit, ext=args.ext, root=args.root)
        if args.json:
            _print_json(results)
        else:
            print(format_search_results(results))
        return 0
    finally:
        conn.close()


def cmd_get(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        data = get_file(conn, args.path, max_chars=args.max_chars)
        if args.json or args.content:
            _print_json(data)
        else:
            print(f"路径：{data['path']}")
            print(f"存在：{data['exists']}  目录：{data['is_dir']}")
            if data.get("size") is not None:
                print(f"大小：{data.get('size_text')}  状态：{data.get('content_status', '')}")
            if data.get("content"):
                print("--- 内容预览 ---")
                print(data["content"])
        return 0
    finally:
        conn.close()


def cmd_ls(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        entries = list_directory(conn, args.path, depth=args.depth, limit=args.limit)
        if args.json:
            _print_json(entries)
            return 0
        for item in entries:
            kind = "DIR " if item["is_dir"] else "FILE"
            indexed = "✓" if item["indexed"] else " "
            print(f"{kind} {indexed} {item['size_text']:>9}  {item['path']}")
        return 0
    finally:
        conn.close()


def cmd_summarize(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        data = summarize_folder(conn, args.path, max_files=args.max_files)
        if args.json:
            _print_json(data)
        else:
            print(data["summary"])
        return 0
    finally:
        conn.close()


def cmd_ask(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        data = ask(conn, args.question, limit=args.limit)
        if args.json:
            _print_json(data)
        else:
            print(data["answer"])
            if data["sources"]:
                print("\n来源：")
                for index, source in enumerate(data["sources"], 1):
                    print(f"  [{index}] {source['name']}  {source['path']}")
        return 0
    finally:
        conn.close()


def cmd_status(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        data = status(conn)
        if args.json:
            _print_json(data)
            return 0
        print(f"文件：{data['files']}  已提取正文：{data['indexed']}  过大：{data['too_large']}  "
              f"不支持：{data['unsupported']}  错误：{data['errors']}")
        print(f"总大小：{_human_size(data['bytes'])}")
        for root in data["roots"]:
            print(f"  [{root['id']}] {root['path']}  文件 {root['files']}  "
                  f"大小 {_human_size(root['bytes'])}  更新 {root['last_update'] or '-'}")
        return 0
    finally:
        conn.close()


def cmd_embed(args: argparse.Namespace) -> int:
    from .db import embedding_stats

    conn = _connection()
    try:
        if args.stats:
            _print_json(embedding_stats(conn)) if args.json else print(
                "已嵌入：{embedded}/{embeddable}（模型 {models} 个）".format(**embedding_stats(conn))
            )
            return 0
    finally:
        conn.close()

    from .embedder import embed_missing
    from .index_lock import IndexLock

    lock = IndexLock(app_config.HOME / "data" / "embed.lock")
    if not lock.acquire():
        pid = lock.holder_pid()
        print(f"已有 kb embed 在运行（PID {pid or '未知'}），本次跳过。")
        return 0
    try:
        # 嵌入让路交互：与本地模型等前台进程抢 CPU 时，调度器优先保前台（Windows 无 os.nice）
        os.nice(10)
    except (AttributeError, OSError):
        pass
    conn = _connection()
    try:
        stats = embed_missing(conn, limit=args.limit)
        if args.json:
            _print_json(stats)
        else:
            print(
                f"[完成] 新嵌入 {stats['embedded']}，失败 {stats['failed']}，"
                f"耗时 {stats['seconds']}s，模型 {stats['model']}"
            )
        return 0 if stats["failed"] == 0 else 1
    finally:
        conn.close()
        lock.release()


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = run_doctor(include_feishu=args.feishu, include_ollama=args.ollama)
    if args.json:
        _print_json(checks)
        return 1 if any(item["status"] == "fail" for item in checks) else 0
    return print_checks(checks)


def cmd_selfcheck(args: argparse.Namespace) -> int:
    from .selfcheck import format_report, run_selfcheck, write_report

    report = run_selfcheck()
    write_report(report)
    if args.json:
        _print_json(report)
    else:
        print(f"[{report['time']}] {format_report(report)}")
    return 1 if report["status"] == "fail" else 0


def cmd_monitor(args: argparse.Namespace) -> int:
    from .selfcheck import run_monitor

    return run_monitor(args.interval)


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run_mcp
    transport = "streamable-http" if args.http else "stdio"
    run_mcp(transport=transport, host=args.host, port=args.port)
    return 0


def cmd_organize(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        if args.plan:
            if not args.path:
                print("请提供要整理的目录。")
                return 1
            plan = plan_organize(
                conn, args.path, strategy=args.strategy,
                recursive=args.recursive, rename_pattern=args.rename_pattern,
            )
            if args.json:
                _print_json(plan)
                return 0
            print(f"计划：{plan['plan_id']}  策略：{plan['strategy']}  "
                  f"操作：{len(plan['operations'])} 个")
            for operation in plan["operations"][:50]:
                print(f"  {operation['src']}  ->  {operation['dst']}")
            if len(plan["operations"]) > 50:
                print(f"  ... 还有 {len(plan['operations']) - 50} 个")
            print(f"执行：kb organize --apply {plan['plan_id']} --yes")
            return 0
        if args.apply:
            if not args.yes:
                print("执行整理需要加 --yes 确认。")
                return 1
            result = apply_plan(conn, args.apply, confirm=True)
            _print_json(result)
            return 0 if result["failed_count"] == 0 else 1
        if args.undo:
            result = undo_plan(conn, args.undo)
            _print_json(result)
            return 0 if result["failed_count"] == 0 else 1
        print("请指定 --plan、--apply PLAN_ID 或 --undo PLAN_ID。")
        return 1
    finally:
        conn.close()


def _watch_scan(conn) -> float:
    started = time.time()
    try:
        stats = index_roots(conn, progress=lambda _message: None)
        if stats.indexed or stats.removed:
            print(f"[{time.strftime('%H:%M:%S')}] 入库 {stats.indexed}，"
                  f"删除 {stats.removed}，错误 {stats.errors}", flush=True)
        from .auto_embed import maybe_auto_embed
        maybe_auto_embed(app_config.HOME, reason="watch")
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] {exc}", flush=True)
    finally:
        try:
            conn.commit()
        except Exception:  # noqa: BLE001
            pass
    return time.time() - started


def _parse_daily(spec: str) -> tuple[int, int]:
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(spec))
    if not match:
        raise ValueError("--daily 格式应为 HH:MM，例如 10:00")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("--daily 时间超出范围")
    return hour, minute


def _next_daily(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _watch_daily(conn, spec: str, grace_seconds: int = 90) -> int:
    hour, minute = _parse_daily(spec)
    print(f"每天 {hour:02d}:{minute:02d} 做一次增量扫描；关机/睡眠错过不补跑。按 Ctrl+C 停止。",
          flush=True)
    try:
        while True:
            target = _next_daily(datetime.now(), hour, minute)
            while True:
                now = datetime.now()
                lag = (now - target).total_seconds()
                if lag >= 0:
                    if lag <= grace_seconds:
                        _watch_scan(conn)
                    else:
                        print(f"[跳过] 错过 {target:%Y-%m-%d %H:%M}（当前 {now:%H:%M:%S}），不补跑",
                              flush=True)
                    break
                time.sleep(min(20.0, max(1.0, (target - now).total_seconds())))
    except KeyboardInterrupt:
        print("\n已停止监听。", flush=True)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        if not ensure_scope(conn):
            return 1
        daily = str(getattr(args, "daily", "") or "").strip()
        if daily:
            return _watch_daily(conn, daily)
        if bool(getattr(args, "events", True)):
            from .watch_events import run_event_watch, watchdog_available
            if watchdog_available():
                return run_event_watch()
            print("[提示] 未安装 watchdog，退回定时扫描；安装：.venv/bin/pip install 'watchdog>=4.0'",
                  flush=True)
        print(f"静默监听中，每 {args.interval} 秒做一次增量扫描。按 Ctrl+C 停止。", flush=True)
        while True:
            elapsed = _watch_scan(conn)
            time.sleep(max(1.0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("\n已停止监听。", flush=True)
        return 0
    finally:
        conn.close()


def cmd_feishu(args: argparse.Namespace) -> int:
    conn = _connection()
    try:
        if not ensure_scope(conn):
            return 1
    finally:
        conn.close()
    from .feishu_bot import run_bot
    return run_bot()


def cmd_scan(args: argparse.Namespace) -> int:
    from .privacy import scan
    root = Path(args.path).expanduser().resolve() if args.path else app_config.HOME
    findings = scan(root)
    if args.json:
        _print_json(findings)
    elif not findings:
        print(f"隐私扫描通过：{root}")
    else:
        print(f"发现 {len(findings)} 处可能的敏感内容：")
        for item in findings:
            print(f"  [{item['kind']}/{item['pattern']}] {item['file']}:{item['line']}  {item['excerpt']}")
    return 1 if findings else 0


def cmd_scan_junk(args: argparse.Namespace) -> int:
    from .junk_scan import scan_junk, trash_junk
    roots = [Path(p).expanduser() for p in args.paths] or None
    report = scan_junk(roots, max_files=args.max_files)
    if args.trash:
        if not args.yes:
            print("移入废纸篓需要二次确认：请加上 --yes")
            return 2
        result = trash_junk(report, limit=args.limit)
        if args.json:
            _print_json(result)
        else:
            print(f"已移入废纸篓 {result['moved_count']} 项（可恢复），失败 {result['failed_count']} 项")
            for item in result["failed"][:10]:
                print(f"  失败：{item['path']}  {item['error']}")
        return 0 if result["ok"] else 1
    if args.json:
        _print_json(report)
        return 0
    labels = {
        "temp": "临时文件", "empty": "空文件", "old_log": "陈旧日志",
        "duplicate": "重复文件", "stale_cache": "缓存目录大文件",
        "backup": "陈旧备份", "software": "软件缓存数据（飞书/微信/Chrome/360）",
    }
    print(f"扫描根目录：{'、'.join(report['scanned_roots'])}")
    print(f"共扫描 {report['scanned_files']} 个文件；可清理 {report['total']['count']} 项 / {report['total']['bytes_text']}"
          + ("（达到扫描上限，已截断）" if report["truncated"] else ""))
    for category, entry in report["categories"].items():
        print(f"\n[{category}] {labels.get(category, category)}  {entry['count']} 项 / {entry['bytes_text']}"
              + ("（只建议移出索引，不支持删除）" if category not in report["trashable_categories"] else ""))
        for item in entry["items"][:10]:
            print(f"  {item['size_text']:>10s}  {item['path']}  —— {item['reason']}")
        if entry["count"] > 10:
            print(f"  … 其余 {entry['count'] - 10} 项")
    print(f"\n{report['note']}")
    print("执行清理：kb scan-junk --trash --yes（只处理白名单类别，移入系统废纸篓可恢复）")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .export import export_bundle
    result = export_bundle(args.output, mode=args.mode)
    _print_json(result)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from .export import import_bundle
    result = import_bundle(args.source, force=args.force)
    _print_json(result)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    from . import runtime

    conn = _connection()
    try:
        if not ensure_scope(conn):
            return 1
    finally:
        conn.close()

    existing = runtime.read_runtime()
    if existing.get("pids") or existing.get("parent_pid"):
        print("检测到上一次启动的服务，先关闭旧服务...", flush=True)
        runtime.stop_all()
        time.sleep(0.8)

    config = app_config.load_config()
    if str(config.get("models", {}).get("mode", "none")).lower() == "ollama":
        from . import llm

        if not llm.start_server():
            print("本地模型服务未启动，问答会回退到摘录式。", flush=True)
        else:
            llm.warmup_async(llm.model_name(config))
    map_host = "127.0.0.1"
    map_port = 8770
    mcp_host = str(config["mcp"].get("host", "127.0.0.1"))
    if mcp_host in {"0.0.0.0", "::", ""}:
        mcp_host = "127.0.0.1"
    if not args.no_web:
        print(f"3D 脑图：http://{map_host}:{map_port}/", flush=True)
    if not args.no_mcp:
        print(f"MCP 接口：http://{mcp_host}:{config['mcp'].get('port', 8766)}/mcp "
              "（给豆包/机器人用，不是网页）", flush=True)

    port_checks = []
    if not args.no_web:
        port_checks.append(("3D 脑图", map_host, map_port))
    if not args.no_mcp:
        port_checks.append((
            "MCP",
            str(config["mcp"].get("host", "127.0.0.1")),
            int(config["mcp"].get("port", 8766)),
        ))
    for name, host, port in port_checks:
        if not runtime.port_in_use(host, port):
            continue
        print(f"{name}端口 {port} 已被占用，先尝试关闭同一套旧服务...", flush=True)
        starters = runtime.stop_starters()
        if starters:
            print(f"已关闭旧的 kb start 守护进程：{starters}", flush=True)
        result = runtime.stop_ports([port], host=host, only_kb=True)
        deadline = time.time() + 5
        while time.time() < deadline and runtime.port_in_use(host, port):
            time.sleep(0.2)
        if not runtime.port_in_use(host, port):
            continue
        pids = runtime.port_pids(port)
        pid_text = f"（PID: {', '.join(str(pid) for pid in pids)}）" if pids else ""
        foreign = result.get("foreign", [])
        if foreign:
            print(f"端口 {port} 被其他程序占用{pid_text}，不会自动关闭。", flush=True)
        else:
            print(f"端口 {port} 仍被占用{pid_text}，自动关闭失败。", flush=True)
        print("先运行 `kb stop --force`，或手动关闭占用端口的程序后重试。", flush=True)
        return 1

    services = []
    if not args.no_web:
        services.append(("map", [sys.executable, "-m", "kb", "map",
                                 "--host", map_host, "--port", str(map_port)]))
    if not args.no_mcp:
        services.append(("mcp", [sys.executable, "-m", "kb", "mcp", "--http"]))
    if not args.no_watch:
        watch_command = [sys.executable, "-m", "kb", "watch"]
        if getattr(args, "daily", ""):
            watch_command.extend(["--daily", args.daily])
        elif getattr(args, "events", True):
            watch_command.append("--events")
        else:
            watch_command.extend(["--no-events", "--interval", str(args.interval)])
        services.append(("watch", watch_command))
    if args.with_feishu or (not args.no_feishu and app_config.feishu_ready()):
        services.append(("feishu", [sys.executable, "-m", "kb", "feishu"]))
    if not getattr(args, "no_monitor", False):
        services.append(("monitor", [
            sys.executable, "-m", "kb", "monitor",
            "--interval", str(getattr(args, "monitor_interval", 1800)),
        ]))
    if not services:
        return 0

    service_map = dict(services)
    processes: dict[str, subprocess.Popen] = {}
    started_at: dict[str, float] = {}
    failures: dict[str, int] = {}

    def spawn(name: str, command: list[str]) -> None:
        print("启动：" + " ".join(command), flush=True)
        process = subprocess.Popen(command)
        processes[name] = process
        started_at[name] = time.time()
        runtime.write_runtime([item.pid for item in processes.values()])

    try:
        for name, command in services:
            spawn(name, command)
        while True:
            time.sleep(1)
            for name in list(processes):
                process = processes[name]
                code = process.poll()
                if code is None:
                    continue
                processes.pop(name, None)
                uptime = time.time() - started_at.get(name, 0.0)
                if uptime < 5:
                    failures[name] = failures.get(name, 0) + 1
                else:
                    failures[name] = 0
                if failures[name] > 5:
                    print(f"{name} 连续失败 {failures[name]} 次，停止自动重启。", flush=True)
                    continue
                delay = min(30, max(2, 2 ** max(0, failures[name] - 1)))
                print(f"{name} 已退出（code={code}），{delay} 秒后自动重启...", flush=True)
                time.sleep(delay)
                spawn(name, service_map[name])
                runtime.write_runtime([item.pid for item in processes.values()])
            if not processes:
                print("所有服务都已停止。", flush=True)
                return 1
    except KeyboardInterrupt:
        print("\n正在停止...", flush=True)
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        runtime.clear_runtime(os.getpid())
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    from . import runtime

    result = runtime.stop_all()
    if getattr(args, "force", False):
        config = app_config.load_config()
        ports = [
            8770,
            int(config["mcp"].get("port", 8766)),
        ]
        result["starters"] = runtime.stop_starters()
        result["ports"] = runtime.stop_ports(ports, only_kb=False)
    _print_json(result)
    failed = bool(result.get("failed"))
    if getattr(args, "force", False):
        failed = failed or bool(result.get("ports", {}).get("failed"))
    return 0 if not failed else 1


def cmd_logs(args: argparse.Namespace) -> int:
    from . import logging_setup

    known = set(logging_setup.LOG_NAMES)
    if not args.name:
        entries = logging_setup.list_logs()
        if args.json:
            _print_json(entries)
            return 0
        print(f"日志目录：{app_config.logs_dir()}")
        if not entries:
            print("还没有日志。启动服务后会自动写入。")
            return 0
        for entry in entries:
            print(f"  {entry['name']:<8}{entry['size_text']:>9}  "
                  f"{entry['modified']}  {entry['path']}")
        print("查看：kb logs map   跟踪：kb logs map -f   清空：kb logs map --clear")
        return 0

    name = args.name
    path = logging_setup.log_path(name)
    existing = {entry["name"] for entry in logging_setup.list_logs()}
    if name not in known and name not in existing:
        print(f"没有这个日志：{name}（可选：{'、'.join(sorted(known))}）")
        return 1
    if args.clear:
        try:
            path.write_text("", encoding="utf-8")
        except OSError as exc:
            print(f"清空失败：{exc}")
            return 1
        print(f"已清空：{path}")
        return 0
    if not path.exists():
        print(f"还没有 {name} 日志。启动对应服务后会自动写入：{path}")
        return 0
    if args.json:
        _print_json({
            "name": name,
            "path": str(path),
            "lines": logging_setup.tail_lines(path, args.lines),
        })
        return 0
    print(f"--- {path}（最后 {args.lines} 行）---")
    for line in logging_setup.tail_lines(path, args.lines):
        print(line, end="" if line.endswith("\n") else "\n")
    if args.follow:
        print("--- 跟踪中，按 Ctrl+C 停止 ---")
        logging_setup.follow(path)
    return 0


def cmd_model(args: argparse.Namespace) -> int:
    from . import llm

    action = getattr(args, "action", "status")
    if action == "status":
        data = llm.status()
        if getattr(args, "json", False):
            _print_json(data)
        else:
            print(f"Ollama：{'已安装' if data['installed'] else '未安装'}；"
                  f"服务：{'运行中' if data['running'] else '未运行'}")
            print(f"配置：mode={data['mode']}，model={data['configured_model']}")
            if data["models"]:
                print("已安装模型：" + "、".join(data["models"]))
            if data["error"]:
                print(f"错误：{data['error']}")
        return 0 if data["installed"] else 1

    if action == "disable":
        print(json.dumps(llm.disable(), ensure_ascii=False, indent=2))
        return 0

    name = (getattr(args, "name", "") or llm.model_name()).strip() or llm.DEFAULT_MODEL
    if action == "install":
        if not llm.installed():
            print("未安装 Ollama，尝试通过 Homebrew 安装…")
            if not llm.install_ollama():
                print("安装失败。请手动安装：brew install ollama")
                return 1
        if not llm.start_server():
            print("Ollama 服务没有启动。可以手动运行：ollama serve")
            return 1
        print(f"下载模型：{name}（大小取决于模型）")
        if not llm.pull(name):
            print("模型下载失败。")
            return 1
        result = llm.enable(name)
        llm.warmup_async(result["model"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if action == "enable":
        if not llm.installed():
            print("未安装 Ollama。先运行：kb model install --name " + name)
            return 1
        if not llm.start_server():
            print("Ollama 服务没有启动。")
            return 1
        if not llm.model_available(name):
            print(f"模型 {name} 还没下载。先运行：kb model install --name {name}")
            return 1
        result = llm.enable(name)
        llm.warmup_async(result["model"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"未知操作：{action}")
    return 1


def cmd_map(args: argparse.Namespace) -> int:
    from .map_server import run_map

    args.kb = str(app_config.HOME)
    return run_map(args)


def cmd_setup(args: argparse.Namespace) -> int:
    from .setup_wizard import run_setup
    return run_setup(args)


def cmd_tray(args: argparse.Namespace) -> int:
    from .tray import run_tray
    return run_tray(check=args.check)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb", description="本地文件知识库 + MCP 服务")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="初始化数据目录和数据库")
    init_parser.add_argument("--root", action="append", help="初始化时加入索引范围，可重复")
    init_parser.add_argument("--doctor", action="store_true", help="初始化后运行自检")
    init_parser.add_argument("--feishu", action="store_true", help="自检时测试飞书网络")
    init_parser.add_argument("--ollama", action="store_true", help="自检时测试 Ollama")
    init_parser.set_defaults(func=cmd_init)

    scope_parser = sub.add_parser("scope", help="查看或修改索引范围")
    scope_parser.add_argument("--add", action="append", help="添加文件夹或盘符，可重复")
    scope_parser.add_argument("--remove", action="append", help="移除文件夹或盘符，可重复")
    scope_parser.add_argument("--clear", action="store_true", help="清空全部范围")
    scope_parser.add_argument("--home", action="store_true", help="加入用户目录")
    scope_parser.add_argument("--all", action="store_true", help="Windows 下加入所有盘符")
    scope_parser.add_argument("--list", action="store_true", help="只列出当前范围")
    scope_parser.add_argument("--interactive", action="store_true", help="强制打开交互菜单")
    scope_parser.set_defaults(func=cmd_scope)

    index_parser = sub.add_parser("index", help="建立或更新索引")
    index_parser.add_argument("--root", help="只索引指定根目录")
    index_parser.add_argument("--full", action="store_true", help="忽略缓存，全量重建")
    index_parser.add_argument("--quiet", action="store_true", help="不打印进度")
    index_parser.add_argument("--json", action="store_true", help="输出 JSON")
    index_parser.set_defaults(func=cmd_index)

    search_parser = sub.add_parser("search", help="搜索文件")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--ext", default="")
    search_parser.add_argument("--root", default="")
    search_parser.add_argument("--json", action="store_true")
    search_parser.set_defaults(func=cmd_search)

    get_parser = sub.add_parser("get", help="读取文件信息或内容预览")
    get_parser.add_argument("path")
    get_parser.add_argument("--max-chars", type=int, default=5000)
    get_parser.add_argument("--content", action="store_true", help="只输出 JSON 内容")
    get_parser.add_argument("--json", action="store_true")
    get_parser.set_defaults(func=cmd_get)

    ls_parser = sub.add_parser("ls", help="列出目录")
    ls_parser.add_argument("path")
    ls_parser.add_argument("--depth", type=int, default=1)
    ls_parser.add_argument("--limit", type=int, default=200)
    ls_parser.add_argument("--json", action="store_true")
    ls_parser.set_defaults(func=cmd_ls)

    summarize_parser = sub.add_parser("summarize", help="总结文件夹")
    summarize_parser.add_argument("path")
    summarize_parser.add_argument("--max-files", type=int, default=2000)
    summarize_parser.add_argument("--json", action="store_true")
    summarize_parser.set_defaults(func=cmd_summarize)

    ask_parser = sub.add_parser("ask", help="基于本地索引回答问题")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--limit", type=int, default=5)
    ask_parser.add_argument("--json", action="store_true")
    ask_parser.set_defaults(func=cmd_ask)

    embed_parser = sub.add_parser("embed", help="给文件正文计算语义向量（混合检索用，可反复执行断点续跑）")
    embed_parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个（调试用）")
    embed_parser.add_argument("--stats", action="store_true", help="只查看嵌入进度")
    embed_parser.add_argument("--json", action="store_true")
    embed_parser.set_defaults(func=cmd_embed)

    status_parser = sub.add_parser("status", help="查看索引状态")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=cmd_status)

    doctor_parser = sub.add_parser("doctor", help="环境自检")
    doctor_parser.add_argument("--feishu", action="store_true", help="测试飞书网络")
    doctor_parser.add_argument("--ollama", action="store_true", help="测试 Ollama")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(func=cmd_doctor)

    selfcheck_parser = sub.add_parser("selfcheck", help="运行一次系统自检并写日志")
    selfcheck_parser.add_argument("--json", action="store_true")
    selfcheck_parser.set_defaults(func=cmd_selfcheck)

    monitor_parser = sub.add_parser("monitor", help="定时系统自检（默认 30 分钟）")
    monitor_parser.add_argument("--interval", type=int, default=1800)
    monitor_parser.set_defaults(func=cmd_monitor)

    mcp_parser = sub.add_parser("mcp", help="启动 MCP 服务")
    mcp_parser.add_argument("--stdio", action="store_true", help="stdio 模式（默认）")
    mcp_parser.add_argument("--http", action="store_true", help="Streamable HTTP 模式")
    mcp_parser.add_argument("--host", default=None)
    mcp_parser.add_argument("--port", type=int, default=None)
    mcp_parser.set_defaults(func=cmd_mcp)

    organize_parser = sub.add_parser("organize", help="整理文件夹")
    organize_parser.add_argument("path", nargs="?", default="")
    organize_parser.add_argument("--plan", action="store_true", help="只生成计划")
    organize_parser.add_argument("--apply", metavar="PLAN_ID", help="执行计划")
    organize_parser.add_argument("--undo", metavar="PLAN_ID", help="撤销计划")
    organize_parser.add_argument("--strategy", choices=["type", "date", "content", "image"], default="type")
    organize_parser.add_argument("--recursive", action="store_true")
    organize_parser.add_argument("--rename-pattern", default="")
    organize_parser.add_argument("--yes", action="store_true", help="确认执行")
    organize_parser.add_argument("--json", action="store_true")
    organize_parser.set_defaults(func=cmd_organize)

    watch_parser = sub.add_parser("watch", help="后台静默增量更新")
    watch_parser.add_argument("--interval", type=int, default=30)
    watch_parser.add_argument("--daily", default="", metavar="HH:MM",
                              help="每天固定时间扫描一次；关机/睡眠错过不补跑，例如 10:00")
    watch_parser.add_argument("--events", action=argparse.BooleanOptionalAction, default=True,
                              help="用文件事件监听，只索引变动的文件（默认开启）；--no-events 改用定时全量扫描")
    watch_parser.set_defaults(func=cmd_watch)

    start_parser = sub.add_parser("start", help="同时启动 3D 脑图、MCP HTTP 和监听")
    start_parser.add_argument("--interval", type=int, default=30)
    start_parser.add_argument("--daily", default="", metavar="HH:MM",
                              help="watch 每天固定时间扫描；关机/睡眠错过不补跑，例如 10:00")
    start_parser.add_argument("--events", action=argparse.BooleanOptionalAction, default=True,
                              help="watch 用文件事件监听（默认开启）；--no-events 改用定时全量扫描")
    start_parser.add_argument("--no-web", "--no-map", dest="no_web", action="store_true",
                              help="不启动 3D 脑图")
    start_parser.add_argument("--no-mcp", action="store_true")
    start_parser.add_argument("--no-watch", action="store_true")
    start_parser.add_argument("--with-feishu", action="store_true", help="同时启动飞书机器人")
    start_parser.add_argument("--no-feishu", action="store_true",
                              help="即使配置了飞书也不启动机器人")
    start_parser.add_argument("--no-monitor", action="store_true", help="不启动定时自检")
    start_parser.add_argument("--monitor-interval", type=int, default=1800,
                              help="自检间隔秒数，默认 1800（30 分钟）")
    start_parser.set_defaults(func=cmd_start)

    stop_parser = sub.add_parser("stop", help="关闭由 kb start 启动的后台服务")
    stop_parser.add_argument("--force", action="store_true",
                             help="同时清理占用配置端口的进程")
    stop_parser.set_defaults(func=cmd_stop)

    feishu_parser = sub.add_parser("feishu", help="启动飞书机器人（长连接）")
    feishu_parser.set_defaults(func=cmd_feishu)

    scan_parser = sub.add_parser("scan", help="扫描个人信息/密钥泄露")
    scan_parser.add_argument("--path", default="")
    scan_parser.add_argument("--json", action="store_true")
    scan_parser.set_defaults(func=cmd_scan)

    junk_parser = sub.add_parser("scan-junk", help="扫描电脑上的可清理文件（通用规则：临时/空/旧日志/重复/缓存）")
    junk_parser.add_argument("paths", nargs="*", help="要扫描的目录；默认下载/临时/缓存目录")
    junk_parser.add_argument("--json", action="store_true")
    junk_parser.add_argument("--trash", action="store_true", help="把白名单类别移入系统废纸篓（可恢复）")
    junk_parser.add_argument("--yes", action="store_true", help="配合 --trash 二次确认")
    junk_parser.add_argument("--limit", type=int, default=200, help="--trash 最多处理多少项")
    junk_parser.add_argument("--max-files", type=int, default=200000, help="最多扫描多少个文件")
    junk_parser.set_defaults(func=cmd_scan_junk)

    export_parser = sub.add_parser("export", help="打包分享或迁移")
    export_parser.add_argument("--output", "-o", required=True)
    export_parser.add_argument("--mode", choices=["share", "migrate"], default="share")
    export_parser.set_defaults(func=cmd_export)

    import_parser = sub.add_parser("import", help="导入迁移包")
    import_parser.add_argument("source")
    import_parser.add_argument("--force", action="store_true")
    import_parser.set_defaults(func=cmd_import)

    log_parser = sub.add_parser("logs", help="查看服务日志")
    log_parser.add_argument("name", nargs="?", default="",
                            help="map / mcp / watch / feishu / tray；不填则列出全部")
    log_parser.add_argument("--lines", "-n", type=int, default=50, help="显示最后多少行")
    log_parser.add_argument("--follow", "-f", action="store_true", help="持续跟踪新增内容")
    log_parser.add_argument("--clear", action="store_true", help="清空指定日志")
    log_parser.add_argument("--json", action="store_true")
    log_parser.set_defaults(func=cmd_logs)

    setup_parser = sub.add_parser("setup", help="环境检测、接入引导和一键启动")
    setup_parser.add_argument("--check", action="store_true",
                              help="只检测，不修改配置、不打开窗口、不启动服务")
    setup_parser.add_argument("--json", action="store_true", help="输出 JSON")
    setup_parser.add_argument("--start", action="store_true", help="检测后直接启动服务")
    setup_parser.add_argument("--no-open", action="store_true",
                              help="不自动打开系统设置或应用")
    setup_parser.add_argument("--with-vision", action="store_true", help="安装 OpenCV 图片整理")
    setup_parser.add_argument("--no-vision", action="store_true", help="不提示安装 OpenCV")
    setup_parser.add_argument("--with-ollama", action="store_true",
                              help="安装 Ollama（如需要）、下载并启用本地模型")
    setup_parser.add_argument("--no-ollama", action="store_true", help="关闭本地模型问答")
    setup_parser.add_argument("--ollama-model", default="", help="模型名，例如 qwen2.5:3b")
    setup_parser.set_defaults(func=cmd_setup)

    tray_parser = sub.add_parser("tray", help="启动 macOS 菜单栏控制")
    tray_parser.add_argument("--check", action="store_true", help="只检查托盘依赖和服务状态")
    tray_parser.set_defaults(func=cmd_tray)

    model_parser = sub.add_parser("model", help="本地 Ollama 模型安装与开关")
    model_sub = model_parser.add_subparsers(dest="action", required=True)
    model_status = model_sub.add_parser("status", help="查看 Ollama 和模型状态")
    model_status.add_argument("--json", action="store_true")
    model_status.set_defaults(func=cmd_model)
    model_install = model_sub.add_parser("install", help="安装 Ollama（如需要）并下载模型")
    model_install.add_argument("--name", default="", help="模型名，例如 qwen2.5:3b")
    model_install.set_defaults(func=cmd_model)
    model_enable = model_sub.add_parser("enable", help="启用已安装模型的问答")
    model_enable.add_argument("--name", default="", help="模型名")
    model_enable.set_defaults(func=cmd_model)
    model_disable = model_sub.add_parser("disable", help="关闭模型问答，恢复摘录式")
    model_disable.set_defaults(func=cmd_model)

    map_parser = sub.add_parser("map", help="启动本地 3D 知识库脑图")
    map_parser.add_argument("--host", default="127.0.0.1")
    map_parser.add_argument("--port", type=int, default=8770)
    map_parser.add_argument("--max-files", type=int, default=500)
    map_parser.add_argument("--interval", type=float, default=60.0,
                            help="检查 kb.db 变化的秒数")
    map_parser.add_argument("--index-on-start", action="store_true",
                            help="启动时触发一次增量索引")
    map_parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    map_parser.set_defaults(func=cmd_map)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service_name = {
        "map": "map",
        "mcp": "mcp",
        "watch": "watch",
        "feishu": "feishu",
        "tray": "tray",
    }.get(args.command)
    if service_name:
        from .logging_setup import setup_service_log

        stdio = args.command == "mcp" and not getattr(args, "http", False)
        path = setup_service_log(service_name, mirror_stdout=not stdio)
        if not stdio:
            print(f"日志：{path}", flush=True)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{exc}", file=sys.stderr)
        return 1
