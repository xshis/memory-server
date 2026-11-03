from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import extract, vision
from .config import load_organize_rules, load_config, plans_dir, undo_dir
from .db import ensure_allowed_path, get_file_by_path, now_iso, root_for_path

CATEGORY_BY_EXT: dict[str, str] = {
    ".md": "文档", ".markdown": "文档", ".txt": "文档", ".text": "文档",
    ".pdf": "文档", ".doc": "文档", ".docx": "文档", ".rtf": "文档", ".odt": "文档",
    ".xls": "表格", ".xlsx": "表格", ".csv": "表格", ".tsv": "表格",
    ".ppt": "演示", ".pptx": "演示",
    ".jpg": "图片", ".jpeg": "图片", ".png": "图片", ".gif": "图片",
    ".bmp": "图片", ".webp": "图片", ".heic": "图片", ".svg": "图片",
    ".mp3": "音频", ".wav": "音频", ".flac": "音频", ".aac": "音频", ".m4a": "音频",
    ".mp4": "视频", ".mov": "视频", ".avi": "视频", ".mkv": "视频", ".wmv": "视频", ".flv": "视频",
    ".zip": "压缩包", ".rar": "压缩包", ".7z": "压缩包", ".tar": "压缩包",
    ".gz": "压缩包", ".bz2": "压缩包", ".xz": "压缩包",
    ".exe": "安装包", ".msi": "安装包", ".dmg": "安装包", ".pkg": "安装包",
    ".deb": "安装包", ".rpm": "安装包",
    ".py": "代码", ".js": "代码", ".mjs": "代码", ".cjs": "代码", ".ts": "代码",
    ".tsx": "代码", ".jsx": "代码", ".java": "代码", ".kt": "代码", ".go": "代码",
    ".rs": "代码", ".c": "代码", ".h": "代码", ".cpp": "代码", ".hpp": "代码",
    ".cs": "代码", ".rb": "代码", ".php": "代码", ".swift": "代码",
    ".sql": "代码", ".sh": "代码", ".bash": "代码", ".zsh": "代码",
    ".ps1": "代码", ".bat": "代码", ".cmd": "代码",
}


def _category_by_type(path: Path) -> str:
    return CATEGORY_BY_EXT.get(path.suffix.lower(), "其他")


def _category_by_content(path: Path, rules: dict[str, list[str]], max_chars: int = 200_000) -> str:
    try:
        text, status, _ = extract.extract_text(path)
    except Exception:
        return "其他"
    if status != "indexed" or not text:
        return "其他"
    text = text[:max_chars]
    haystack = f"{path.name}\n{text}"
    scores = {category: sum(haystack.count(word) for word in words) for category, words in rules.items()}
    best = max(scores.items(), key=lambda item: item[1]) if scores else ("其他", 0)
    return best[0] if best[1] > 0 else "其他"


def _unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    stem, suffix = destination.stem, destination.suffix
    for index in range(1, 10000):
        candidate = destination.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成不冲突的文件名：{destination}")


def _iter_target_files(target: Path, recursive: bool) -> list[Path]:
    config = load_config()
    exclude_globs = list(config["index"].get("exclude_globs", []))
    exclude_dirs = {str(item).lower() for item in config["index"].get("exclude_dirs", [])}
    files: list[Path] = []
    if recursive:
        for current, dirnames, filenames in os.walk(
            target, followlinks=False, onerror=lambda _error: None
        ):
            dirnames[:] = [name for name in dirnames if name.lower() not in exclude_dirs]
            for filename in filenames:
                candidate = Path(current) / filename
                try:
                    if candidate.is_symlink():
                        continue
                except OSError:
                    continue
                if extract.is_excluded(candidate, exclude_globs) or extract.is_sensitive(candidate):
                    continue
                files.append(candidate)
    else:
        try:
            for candidate in sorted(target.iterdir(), key=lambda item: item.name.lower()):
                try:
                    if not candidate.is_file() or candidate.is_symlink():
                        continue
                except OSError:
                    continue
                if extract.is_excluded(candidate, exclude_globs) or extract.is_sensitive(candidate):
                    continue
                files.append(candidate)
        except OSError as exc:
            raise RuntimeError(f"目录读取失败：{target}：{exc}") from exc
    return files


def _new_name(path: Path, pattern: str, category: str) -> str:
    stat = path.stat()
    values = {
        "name": path.stem,
        "ext": path.suffix.lstrip("."),
        "category": category,
        "date": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
        "year": datetime.fromtimestamp(stat.st_mtime).strftime("%Y"),
        "month": datetime.fromtimestamp(stat.st_mtime).strftime("%m"),
    }
    try:
        rendered = pattern.format(**values)
    except KeyError:
        rendered = pattern
    rendered = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", rendered).strip().strip(".")
    if not rendered:
        rendered = path.stem
    if path.suffix and not rendered.lower().endswith(path.suffix.lower()):
        rendered += path.suffix
    return rendered


def plan_organize(
    conn: sqlite3.Connection,
    target: str | Path,
    *,
    strategy: str = "type",
    recursive: bool = False,
    rename_pattern: str = "",
) -> dict[str, Any]:
    target_path = ensure_allowed_path(conn, target)
    if not target_path.is_dir():
        raise NotADirectoryError(f"不是目录：{target_path}")
    if strategy not in {"type", "date", "content", "image"}:
        raise ValueError("strategy 只支持 type / date / content / image")
    if strategy == "image":
        vision.ensure_available()

    rules = load_organize_rules()
    files = _iter_target_files(target_path, recursive)
    operations: list[dict[str, Any]] = []

    for path in files:
        if strategy == "image" and path.suffix.lower() not in vision.IMAGE_EXTS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if strategy == "date":
            category = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m")
        elif strategy == "content":
            category = _category_by_content(path, rules)
        elif strategy == "image":
            try:
                category = vision.classify_image(path)
            except Exception:  # noqa: BLE001
                category = "其他"
        else:
            category = _category_by_type(path)

        new_name = _new_name(path, rename_pattern, category) if rename_pattern else path.name
        destination = target_path / category / new_name
        if destination == path:
            continue
        destination = _unique_destination(destination)
        operations.append({
            "action": "move" if destination.name == path.name else "move_rename",
            "src": str(path),
            "dst": str(destination),
            "category": category,
            "reason": f"strategy={strategy}",
        })

    plan_id = uuid.uuid4().hex[:12]
    plan = {
        "plan_id": plan_id,
        "target": str(target_path),
        "strategy": strategy,
        "recursive": recursive,
        "rename_pattern": rename_pattern,
        "created_at": now_iso(),
        "status": "planned",
        "operations": operations,
    }
    plans_dir().mkdir(parents=True, exist_ok=True)
    (plans_dir() / f"{plan_id}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return plan


def load_plan(plan_id: str) -> dict[str, Any]:
    path = plans_dir() / f"{plan_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"整理计划不存在：{plan_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _update_db_path(conn: sqlite3.Connection, src: Path, dst: Path) -> None:
    row = get_file_by_path(conn, src)
    if row is None:
        return
    root = root_for_path(conn, dst) or root_for_path(conn, src)
    if root is None:
        return
    try:
        rel_path = str(dst.relative_to(Path(root["path"])))
    except ValueError:
        rel_path = dst.name
    file_id = int(row["id"])
    conn.execute("DELETE FROM files_fts WHERE rowid = ?", (file_id,))
    conn.execute(
        "UPDATE files SET path=?, rel_path=?, name=?, ext=?, updated_at=? WHERE id=?",
        (str(dst), rel_path, dst.name, dst.suffix.lower(), now_iso(), file_id),
    )
    conn.execute(
        "INSERT INTO files_fts(rowid, name, rel_path, content) VALUES(?, ?, ?, ?)",
        (file_id, dst.name, rel_path, row["content"] or ""),
    )
    conn.commit()


def apply_plan(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    if not confirm:
        raise ValueError("执行整理需要确认：请传 confirm=True")
    plan = load_plan(plan_id)
    if plan.get("status") == "applied":
        raise RuntimeError(f"计划 {plan_id} 已经执行过")

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for operation in plan["operations"]:
        src = Path(operation["src"])
        dst = Path(operation["dst"])
        try:
            ensure_allowed_path(conn, src)
            ensure_allowed_path(conn, dst)
            if not src.exists():
                failed.append({"src": str(src), "error": "源文件不存在"})
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            final_dst = _unique_destination(dst)
            shutil.move(str(src), str(final_dst))
            _update_db_path(conn, src, final_dst)
            applied.append({"src": str(src), "dst": str(final_dst)})
        except Exception as exc:  # noqa: BLE001
            failed.append({"src": str(src), "error": str(exc)})

    undo = {
        "plan_id": plan_id,
        "target": plan.get("target", ""),
        "created_at": now_iso(),
        "operations": [{"src": item["dst"], "dst": item["src"]} for item in applied],
    }
    undo_dir().mkdir(parents=True, exist_ok=True)
    (undo_dir() / f"{plan_id}.json").write_text(
        json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    plan["status"] = "applied"
    plan["applied_at"] = now_iso()
    plan["applied_count"] = len(applied)
    plan["failed_count"] = len(failed)
    (plans_dir() / f"{plan_id}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "plan_id": plan_id,
        "applied": applied,
        "failed": failed,
        "applied_count": len(applied),
        "failed_count": len(failed),
    }


def undo_plan(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    path = undo_dir() / f"{plan_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"撤销记录不存在：{plan_id}")
    undo = json.loads(path.read_text(encoding="utf-8"))
    restored: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for operation in reversed(undo.get("operations", [])):
        src = Path(operation["src"])
        dst = Path(operation["dst"])
        try:
            ensure_allowed_path(conn, src)
            ensure_allowed_path(conn, dst)
            if not src.exists():
                failed.append({"src": str(src), "error": "文件不存在，可能已被移动"})
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            final_dst = _unique_destination(dst)
            shutil.move(str(src), str(final_dst))
            _update_db_path(conn, src, final_dst)
            restored.append({"src": str(src), "dst": str(final_dst)})
        except Exception as exc:  # noqa: BLE001
            failed.append({"src": str(src), "error": str(exc)})

    _remove_empty_dirs(Path(undo.get("target", "")) if undo.get("target") else None)
    return {
        "plan_id": plan_id,
        "restored": restored,
        "failed": failed,
        "restored_count": len(restored),
        "failed_count": len(failed),
    }


def _remove_empty_dirs(root: Path | None) -> None:
    if root is None or not root.exists():
        return
    for current, dirnames, filenames in os.walk(root, topdown=False):
        if not dirnames and not filenames:
            try:
                Path(current).rmdir()
            except OSError:
                pass
