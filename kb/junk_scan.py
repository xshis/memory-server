"""通用垃圾文件扫描：在执行清理推荐前，先用通用规则扫一遍电脑上的可删除文件。

设计原则：
- 跨平台通用：只按文件名/扩展名/年龄/内容哈希判断，与操作系统和索引库无关；
- 保守：每个规则带"年龄"门槛，正在使用中的文件（近期有写入）一律跳过；
- 只报告不自动删：只有显式调用 trash_junk 才会移入系统废纸篓（可恢复），
  且缓存类条目永远不允许从这里删除——缓存只建议移出索引范围。
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .map_server import human_size, is_cache_dir, move_to_trash

# ---- 通用规则阈值（可按需调整，全部独立于具体机器）----
# 系统保护对象：任何情况下跳过，既不报告也不允许删除（Windows 系统文件/回收站等）
NEVER_TOUCH_DIR_NAMES = {"$recycle.bin", "system volume information", "config.msi"}
NEVER_TOUCH_FILE_NAMES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys"}

# 各系统垃圾文件名（小写比较）：macOS .DS_Store；Windows Thumbs.db/desktop.ini；Office ~$ 锁
JUNK_OS_NAMES = {".ds_store", "thumbs.db", "ehthumbs.db", "desktop.ini"}
TEMP_STALE_DAYS = 1          # .tmp/.temp/.part/.crdownload/.download/.partial/.crswap 超 1 天算废弃
EMPTY_STALE_DAYS = 7         # 0 字节文件超过 7 天（避开占位锁文件）
LOG_STALE_DAYS = 30          # 日志超过 30 天
LOG_MIN_BYTES = 10 * 1024 * 1024
CACHE_STALE_DAYS = 30        # 缓存目录里超过 30 天未动的文件
CACHE_MIN_BYTES = 50 * 1024 * 1024
BACKUP_STALE_DAYS = 30       # .bak/.old 备份超 30 天
MAX_HASH_BYTES = 2 * 1024 * 1024 * 1024   # 大于 2GB 不做重复哈希
DEFAULT_MAX_FILES = 200_000               # 扫描文件数上限，超出截断并提示
ITEMS_PER_CATEGORY = 50                   # 每类最多展示多少条

# 允许移入废纸篓的类别（白名单）；stale_cache 只建议移出索引，故意不在其中
TRASHABLE = {"temp", "empty", "old_log", "duplicate", "backup"}


def default_roots() -> List[Path]:
    """默认扫描根：下载目录 + 系统临时目录 + 用户缓存目录（存在才纳入）。"""
    home = Path.home()
    candidates: List[Path] = [home / "Downloads"]
    if os.name == "nt":
        for key in ("TEMP", "TMP"):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]))
        candidates.append(home / "AppData" / "Local" / "Temp")
    elif sys.platform == "darwin":
        if os.environ.get("TMPDIR"):
            candidates.append(Path(os.environ["TMPDIR"]))
        candidates.append(home / ".cache")
    else:
        if os.environ.get("TMPDIR"):
            candidates.append(Path(os.environ["TMPDIR"]))
        candidates.append(Path("/tmp"))
        candidates.append(home / ".cache")
    seen: set = set()
    roots: List[Path] = []
    for item in candidates:
        try:
            resolved = item.expanduser()
            key = str(resolved)
            if key in seen or not resolved.is_dir():
                continue
            seen.add(key)
            roots.append(resolved)
        except OSError:
            continue
    return roots


def _age_days(mtime: float, now: float) -> float:
    return (now - mtime) / 86400.0


def _classify(path: Path, size: int, mtime: float, now: float) -> Optional[Tuple[str, str]]:
    """按通用规则给一个文件分类；返回 (类别, 原因) 或 None。"""
    name = path.name.lower()
    age = _age_days(mtime, now)
    if name in JUNK_OS_NAMES or name.startswith(("~$", ".~lock")):
        # ~$*：Office 锁文件；.~lock*#：LibreOffice/WPS 锁文件
        return "temp", "系统/办公软件垃圾文件"
    if name.endswith((".tmp", ".temp")):
        if age >= TEMP_STALE_DAYS:
            return "temp", f"遗留临时文件（{int(age)} 天未动）"
        return None
    if name.endswith((".part", ".crdownload", ".download", ".partial", ".crswap")):
        # .part(FDM/eMule) .crdownload(Chrome) .partial/.crswap(Edge/IE) 下载临时文件
        if age >= TEMP_STALE_DAYS:
            return "temp", f"废弃的下载临时文件（{int(age)} 天未动）"
        return None
    if size == 0:
        if age >= EMPTY_STALE_DAYS:
            return "empty", f"空文件（{int(age)} 天未动）"
        return None
    if path.suffix.lower() == ".log" and age >= LOG_STALE_DAYS and size >= LOG_MIN_BYTES:
        return "old_log", f"陈旧日志（{int(age)} 天，{human_size(size)}）"
    if path.suffix.lower() in {".bak", ".old"} and age >= BACKUP_STALE_DAYS:
        return "backup", f"陈旧备份文件（{int(age)} 天）"
    if age >= CACHE_STALE_DAYS and size >= CACHE_MIN_BYTES and _in_cache_dir(path):
        return "stale_cache", f"缓存目录内 {int(age)} 天未动的大文件"
    return None


def _in_cache_dir(path: Path) -> bool:
    return any(is_cache_dir(part) for part in path.parts[:-1])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(roots: Iterable[Path], max_files: int) -> Tuple[List[Tuple[Path, os.stat_result]], bool]:
    files: List[Tuple[Path, os.stat_result]] = []
    truncated = False
    for root in roots:
        for current, dirnames, filenames in os.walk(root, followlinks=False, onerror=lambda _e: None):
            # 系统保护目录直接剪枝（Windows 回收站/卷信息/安装回滚目录）
            dirnames[:] = [
                name for name in dirnames
                if name.lower() not in NEVER_TOUCH_DIR_NAMES
            ]
            for filename in filenames:
                if len(files) >= max_files:
                    return files, True
                if filename.lower() in NEVER_TOUCH_FILE_NAMES:
                    continue
                candidate = Path(current) / filename
                try:
                    if candidate.is_symlink():
                        continue
                    stat = candidate.stat()
                except OSError:
                    continue
                if not os.path.isfile(candidate):
                    continue
                files.append((candidate, stat))
    return files, truncated


def scan_junk(
    roots: Optional[List[Path]] = None,
    *,
    max_files: int = DEFAULT_MAX_FILES,
) -> Dict[str, Any]:
    """扫描可清理文件，返回分组报告（只读，不改动任何文件）。"""
    roots = [Path(r).expanduser() for r in (roots or default_roots())]
    now = time.time()
    files, truncated = _iter_files(roots, max_files)

    categories: Dict[str, Dict[str, Any]] = {}
    hash_groups: Dict[str, List[Tuple[Path, os.stat_result]]] = {}

    def bucket(category: str) -> Dict[str, Any]:
        if category not in categories:
            categories[category] = {"count": 0, "bytes": 0, "items": []}
        return categories[category]

    for path, stat in files:
        classified = _classify(path, int(stat.st_size), float(stat.st_mtime), now)
        if classified:
            category, reason = classified
            entry = bucket(category)
            entry["count"] += 1
            entry["bytes"] += int(stat.st_size)
            entry["items"].append({
                "path": str(path),
                "name": path.name,
                "size": int(stat.st_size),
                "size_text": human_size(int(stat.st_size)),
                "reason": reason,
                "age_days": round(_age_days(float(stat.st_mtime), now), 1),
            })
            continue
        # 未命中其他规则且大小合适，才参与重复内容哈希（省时间）
        size = int(stat.st_size)
        if 0 < size <= MAX_HASH_BYTES:
            hash_groups.setdefault(_sha256(path), []).append((path, stat))

    duplicate_rows: List[Tuple[Path, os.stat_result, str]] = []
    for group in hash_groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda item: (len(str(item[0])), float(item[1].st_mtime)))
        keep = ordered[0]
        for path, stat in ordered[1:]:
            duplicate_rows.append((path, stat, f"重复内容，保留：{keep[0].name}"))
    for path, stat, reason in duplicate_rows:
        entry = bucket("duplicate")
        entry["count"] += 1
        entry["bytes"] += int(stat.st_size)
        entry["items"].append({
            "path": str(path),
            "name": path.name,
            "size": int(stat.st_size),
            "size_text": human_size(int(stat.st_size)),
            "reason": reason,
            "age_days": round(_age_days(float(stat.st_mtime), now), 1),
        })

    total_count = 0
    total_bytes = 0
    rendered: Dict[str, Dict[str, Any]] = {}
    for category, entry in sorted(categories.items(), key=lambda kv: kv[1]["bytes"], reverse=True):
        items = sorted(entry["items"], key=lambda item: item["size"], reverse=True)[:ITEMS_PER_CATEGORY]
        total_count += entry["count"]
        total_bytes += entry["bytes"]
        rendered[category] = {
            "count": entry["count"],
            "bytes": entry["bytes"],
            "bytes_text": human_size(entry["bytes"]),
            "items": items,
        }

    return {
        "scanned_roots": [str(r) for r in roots],
        "scanned_files": len(files),
        "truncated": truncated,
        "categories": rendered,
        "total": {"count": total_count, "bytes": total_bytes, "bytes_text": human_size(total_bytes)},
        "trashable_categories": sorted(TRASHABLE),
        "note": "stale_cache 只建议移出索引范围，不提供删除；--trash 只处理白名单类别，移入系统废纸篓可恢复。",
    }


def trash_junk(report: Dict[str, Any], *, limit: int = 200) -> Dict[str, Any]:
    """把报告里的白名单类别（temp/empty/old_log/duplicate）移入系统废纸篓。"""
    moved: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    pending: List[Tuple[Path, str]] = []
    for category in TRASHABLE:
        for item in report.get("categories", {}).get(category, {}).get("items", []):
            pending.append((Path(item["path"]), category))
    for path, category in pending[:limit]:
        try:
            if not path.is_file():
                raise FileNotFoundError("文件已不存在")
            destination = move_to_trash(path)
            moved.append({"path": str(path), "trash": destination, "category": category})
        except Exception as exc:  # noqa: BLE001
            failed.append({"path": str(path), "error": str(exc)})
    return {
        "ok": not failed,
        "moved": moved,
        "failed": failed,
        "moved_count": len(moved),
        "failed_count": len(failed),
    }
