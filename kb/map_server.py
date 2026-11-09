#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import HOME
from .db import delete_file_by_id, ensure_allowed_path

DEFAULT_KB = HOME
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770

CATEGORY_BY_EXT = {
    **{ext: "文档" for ext in (".md", ".markdown", ".txt", ".text", ".pdf", ".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".tsv", ".ppt", ".pptx")},
    **{ext: "代码" for ext in (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".sql", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".dart", ".lua")},
    **{ext: "图片" for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic", ".tif", ".tiff", ".ico")},
    **{ext: "音视频" for ext in (".mp3", ".wav", ".flac", ".aac", ".m4a", ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm")},
    **{ext: "压缩包" for ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".dmg", ".pkg")},
    **{ext: "数据/配置" for ext in (".json", ".jsonl", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".lock")},
    **{ext: "可执行/库" for ext in (".so", ".dll", ".dylib", ".exe", ".msi", ".a", ".o", ".class", ".jar", ".wasm")},
}
CATEGORY_COLORS = {
    "文档": "#60a5fa",
    "代码": "#34d399",
    "图片": "#f472b6",
    "音视频": "#a78bfa",
    "压缩包": "#fbbf24",
    "数据/配置": "#22d3ee",
    "可执行/库": "#f87171",
    "其他": "#94a3b8",
}
CACHE_DIRS = {
    ".cursor", ".nvm", ".pub-cache", ".local", "homebrew-core", ".dartServer",
    "DoubaoWork", "Doubao", ".doubao", ".chrome-xxz-profile", ".kimi-code", ".lldb",
    "mysql-data", ".trae", ".codex", ".codex-deepseek", ".kimi_openclaw", ".lark-cli",
    ".V2rayU", ".openjfx", ".ServiceHub", ".subversion", ".sheetagent", ".workbuddy",
}
PROJECT_PATTERNS = [
    ("personal-kb", re.compile(r"personal-kb", re.I)),
    ("xxd-orders", re.compile(r"xxd-orders", re.I)),
    ("lan-share", re.compile(r"lan-share", re.I)),
    ("memory-server", re.compile(r"memory-server", re.I)),
    ("mall-admin", re.compile(r"mall-admin", re.I)),
    ("live-stream", re.compile(r"live-stream", re.I)),
    ("openclaw", re.compile(r"openclaw", re.I)),
]
QUESTION_HINTS = re.compile(
    r"[?？]|吗|怎么|如何|什么|为什么|哪|谁|多少|是不是|能否|可不可以|介绍|总结|区别|帮我|推荐一下"
)


def human_size(value: int) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{value}B"


def category_of(ext: str) -> str:
    return CATEGORY_BY_EXT.get((ext or "").lower(), "其他")


def glyphs_of(name: str) -> Tuple[str, str]:
    """返回文件点的单字与短词，例如 package.json -> ('P', 'PAC')，安装与使用.md -> ('安', '安装')。"""
    stem = Path(str(name or "")).stem or str(name or "")
    cleaned = stem.lstrip("._- ~")
    if not cleaned:
        cleaned = stem or "?"
    match = re.match(r"[A-Za-z0-9]+", cleaned)
    if match:
        token = match.group(0).upper()
        return token[0], token[:3]
    return cleaned[0], cleaned[:2]


def status_label(status: str) -> Tuple[str, str]:
    if status == "indexed":
        return "status:已索引", "已索引"
    if status == "too_large":
        return "status:过大", "过大"
    if status == "error":
        return "status:错误", "错误"
    return "status:仅文件名", "仅文件名"


def _semantic_edges(db_path: Path, selected_rows: List[Dict[str, Any]],
                    cosine_threshold: float = 0.8, max_per_node: int = 2) -> List[Dict[str, Any]]:
    """展示中的文件两两计算 int8 向量余弦，超过阈值的连边。返回前端 links 列表。

    向量是单位化后的 int8（quantize_vector），cosine = 点积 / (127*127)。
    """
    import numpy as np

    fid_to_node = {}
    ids = []
    for index, row in enumerate(selected_rows):
        fid = row.get("id")
        if fid is None:
            continue
        fid_to_node[int(fid)] = f"file:{index}"
        ids.append(int(fid))
    if len(ids) < 8:
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT file_id, data FROM file_embeddings WHERE file_id IN ({marks})", ids
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 8:
        return []
    dim = len(rows[0]["data"])
    matrix = np.empty((len(rows), dim), dtype=np.int16)
    for i, row in enumerate(rows):
        matrix[i] = np.frombuffer(row["data"], dtype=np.int8)
    # int16 直接相乘会溢出（单点积最大 512×127²≈8.3M > 32767），必须升到 int32 再乘
    dot = matrix.astype(np.int32) @ matrix.astype(np.int32).T
    np.fill_diagonal(dot, -(1 << 20))
    limit = int(cosine_threshold * 127 * 127)
    k = min(max_per_node, len(rows) - 1)
    top = np.argpartition(dot, -k, axis=1)[:, -k:]
    edges: Dict[Tuple[int, int], Tuple[str, str, float]] = {}
    for a in range(len(rows)):
        for b in top[a]:
            score = int(dot[a, b])
            if score < limit:
                continue
            key = (a, int(b)) if a < int(b) else (int(b), a)
            if key in edges:
                continue
            fa, fb = int(rows[a]["file_id"]), int(rows[b]["file_id"])
            na, nb = fid_to_node.get(fa), fid_to_node.get(fb)
            if not na or not nb:
                continue
            edges[key] = (na, nb, round(score / (127.0 * 127.0), 3))
    return [
        {"source": na, "target": nb, "kind": "semantic", "weight": w}
        for (na, nb, w) in edges.values()
    ]


def build_graph(db_path: Path, *, max_files: int = 500,
                row_sink: Optional[List[Dict[str, Any]]] = None,
                progress: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
    def emit(percent: int, message: str) -> None:
        if progress:
            progress(max(0, min(100, percent)), message)

    emit(2, "读取索引库（文件多时需要一会儿）")
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        summary_row = conn.execute(
            """
            SELECT COUNT(*) AS files,
                   COALESCE(SUM(size),0) AS bytes,
                   COALESCE(SUM(content_status='indexed'),0) AS indexed,
                   COALESCE(SUM(content_status='unsupported'),0) AS unsupported,
                   COALESCE(SUM(content_status='too_large'),0) AS too_large,
                   COALESCE(SUM(content_status='error'),0) AS errors
            FROM files
            """
        ).fetchone()
        rows = [dict(row) for row in conn.execute(
            "SELECT id, rel_path, name, ext, size, mtime, content_status, sha256 FROM files"
        )]
        try:
            roots = [dict(row) for row in conn.execute("SELECT path FROM roots")]
        except sqlite3.Error:
            roots = []
    finally:
        conn.close()

    emit(30, "读取文件元数据")

    for row in rows:
        rel = str(row["rel_path"] or "")
        parts = rel.split("/")
        row["top_dir"] = parts[0] if len(parts) > 1 else "(根目录)"
        row["category"] = category_of(str(row["ext"] or ""))
        row["status_tag"], row["status_text"] = status_label(str(row["content_status"] or ""))
        row["project"] = ""
        for name, pattern in PROJECT_PATTERNS:
            if pattern.search(rel):
                row["project"] = name
                break
        row["is_empty"] = int(row["size"] or 0) == 0
        name_lower = str(row["name"] or "").lower()
        row["is_temp"] = (
            name_lower in {".ds_store", "thumbs.db", "desktop.ini"}
            or name_lower.startswith("~$")
            or name_lower.endswith((".tmp", ".temp", ".part", ".crdownload", ".download"))
        )
        row["is_cache"] = row["top_dir"] in CACHE_DIRS

    duplicate_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        digest = str(row.get("sha256") or "")
        # 0 字节文件哈希相同，但归“空文件”建议，不参与重复内容分组
        if digest and not row["is_empty"]:
            duplicate_groups[digest].append(row)
    duplicate_rows = set()
    duplicate_items: List[Dict[str, Any]] = []
    duplicate_bytes = 0
    candidates: List[Dict[str, Any]] = []
    for group in duplicate_groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda item: (len(str(item["rel_path"])), float(item["mtime"] or 0)))
        keep = ordered[0]
        for row in ordered[1:]:
            duplicate_rows.add(row["rel_path"])
            size = int(row["size"] or 0)
            duplicate_bytes += size
            candidates.append({
                "path": row["rel_path"],
                "name": row["name"],
                "size": human_size(size),
                "_bytes": size,
                "reason": f"重复内容，保留：{Path(str(keep['rel_path'])).name}",
            })
    candidates.sort(key=lambda item: item["_bytes"], reverse=True)
    for item in candidates[:20]:
        item.pop("_bytes", None)
        duplicate_items.append(item)
    for row in rows:
        row["is_duplicate"] = row["rel_path"] in duplicate_rows

    ext_counts = Counter((str(row["ext"] or "").lower() or "(无扩展名)") for row in rows)
    dir_counts = Counter(row["top_dir"] for row in rows)
    dir_bytes = Counter()
    dir_category_counts: Dict[str, Counter] = defaultdict(Counter)
    project_category_counts: Dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        dir_bytes[row["top_dir"]] += int(row["size"] or 0)
        dir_category_counts[row["top_dir"]][row["category"]] += 1
        if row["project"]:
            project_category_counts[row["project"]][row["category"]] += 1
    project_counts = Counter(row["project"] for row in rows if row["project"])
    status_counts = Counter(row["status_tag"] for row in rows)
    category_counts = Counter(row["category"] for row in rows)

    empty_rows = [row for row in rows if row["is_empty"]]
    temp_rows = [row for row in rows if row["is_temp"]]
    cache_rows = [row for row in rows if row["is_cache"]]
    empty_bytes = sum(int(row["size"] or 0) for row in empty_rows)
    temp_bytes = sum(int(row["size"] or 0) for row in temp_rows)
    cache_bytes = sum(int(row["size"] or 0) for row in cache_rows)
    cache_by_dir = Counter(row["top_dir"] for row in cache_rows)
    cache_items = [
        {
            "path": name,
            "name": name,
            "size": human_size(dir_bytes[name]),
            "reason": f"工具/缓存目录，{count} 个文件；建议移出索引或手动清理",
        }
        for name, count in cache_by_dir.most_common(15)
    ]
    recommendations = [
        {
            "id": "duplicate", "label": "重复文件", "count": len(duplicate_rows),
            "bytes": duplicate_bytes, "bytes_text": human_size(duplicate_bytes),
            "hint": "同一内容保留一份，其余可删除；这里只做推荐，不会自动删。",
            "items": duplicate_items,
        },
        {
            "id": "empty", "label": "空文件", "count": len(empty_rows),
            "bytes": empty_bytes, "bytes_text": human_size(empty_bytes),
            "hint": "0 字节文件通常没有内容，可确认后删除。",
            "items": [
                {"path": row["rel_path"], "name": row["name"], "size": "0B", "reason": "空文件"}
                for row in empty_rows[:20]
            ],
        },
        {
            "id": "temp", "label": "临时文件", "count": len(temp_rows),
            "bytes": temp_bytes, "bytes_text": human_size(temp_bytes),
            "hint": "*.tmp / *.part / ~$* / .DS_Store 等临时文件，可确认后删除。",
            "items": [
                {"path": row["rel_path"], "name": row["name"], "size": human_size(int(row["size"] or 0)), "reason": "临时文件"}
                for row in temp_rows[:20]
            ],
        },
        {
            "id": "cache", "label": "工具/缓存目录", "count": len(cache_rows),
            "bytes": cache_bytes, "bytes_text": human_size(cache_bytes),
            "hint": "代码编辑器、包管理器、模型缓存等；建议移出索引范围，不一定删除。",
            "items": cache_items,
        },
    ]

    emit(48, "统计推荐与目录")
    top_exts = [ext for ext, _ in ext_counts.most_common(12) if ext]
    user_dirs = [name for name, _ in dir_counts.most_common(40) if name not in CACHE_DIRS and name != "(根目录)"][:10]
    top_projects = [name for name, _ in project_counts.most_common(6)]

    dir_images = Counter(row["top_dir"] for row in rows if row["category"] == "图片")
    root_paths = [Path(str(item.get("path") or "")).expanduser() for item in roots if item.get("path")]
    organize_suggestions: List[Dict[str, Any]] = []
    candidates = [
        name for name, count in dir_counts.items()
        if name not in ("(根目录)",) and name not in CACHE_DIRS
        and not name.startswith(".")
        and (dir_images.get(name, 0) >= 3 or count >= 20)
    ]
    for name in sorted(candidates, key=lambda item: (dir_images.get(item, 0), dir_counts[item]), reverse=True)[:24]:
        absolute = ""
        for root in root_paths:
            candidate = root / name
            if candidate.is_dir():
                absolute = str(candidate)
                break
        if not absolute and root_paths:
            absolute = str(root_paths[0] / name)
        images = dir_images.get(name, 0)
        reason = (f"{images} 张图片，可用 OpenCV 归类"
                  if images >= 3 else f"{dir_counts[name]} 个文件，可按类型或月份整理")
        organize_suggestions.append({
            "dir": name,
            "path": absolute or name,
            "files": int(dir_counts[name]),
            "images": int(images),
            "bytes_text": human_size(int(dir_bytes[name])),
            "reason": reason,
        })

    def add_tag(tag_id: str, label: str, kind: str, count: int, color: str) -> Dict[str, Any]:
        glyph, word = glyphs_of(label)
        return {
            "id": tag_id, "label": label, "kind": "tag", "tagKind": kind,
            "count": int(count), "color": color, "glyph": glyph, "word": word,
        }

    tags: List[Dict[str, Any]] = []
    for category, count in category_counts.most_common():
        tags.append(add_tag(f"cat:{category}", category, "category", count, CATEGORY_COLORS.get(category, "#94a3b8")))
    for ext in top_exts:
        tags.append(add_tag(f"ext:{ext}", ext, "ext", ext_counts[ext], "#22d3ee"))
    for name in user_dirs:
        tags.append(add_tag(f"dir:{name}", name, "dir", dir_counts[name], "#fbbf24"))
    cache_count = sum(count for name, count in dir_counts.items() if name in CACHE_DIRS)
    if cache_count:
        tags.append(add_tag("dir:工具缓存", "工具/缓存", "dir", cache_count, "#64748b"))
    for tag_id, label in (
        ("status:已索引", "已索引"),
        ("status:仅文件名", "仅文件名"),
        ("status:过大", "过大"),
        ("status:错误", "错误"),
    ):
        if status_counts.get(tag_id, 0):
            tags.append(add_tag(tag_id, label, "status", status_counts[tag_id], "#a78bfa"))
    for name in top_projects:
        tags.append(add_tag(f"proj:{name}", name, "project", project_counts[name], "#f97316"))
    for tag_id, label, count, color in (
        ("rec:duplicate", "重复", len(duplicate_rows), "#f97316"),
        ("rec:empty", "空文件", len(empty_rows), "#fb7185"),
        ("rec:temp", "临时文件", len(temp_rows), "#f59e0b"),
        ("rec:cache", "缓存", len(cache_rows), "#64748b"),
    ):
        if count:
            tags.append(add_tag(tag_id, label, "recommend", count, color))

    tag_ids = {tag["id"] for tag in tags}
    top_ext_set = set(top_exts)
    user_dir_set = set(user_dirs)

    def tags_for(row: Dict[str, Any]) -> List[str]:
        result = [f"cat:{row['category']}"]
        if row["top_dir"] in user_dir_set:
            result.append(f"dir:{row['top_dir']}")
        elif row["top_dir"] in CACHE_DIRS:
            result.append("dir:工具缓存")
        if row["project"]:
            result.append(f"proj:{row['project']}")
        ext = str(row["ext"] or "").lower()
        if ext in top_ext_set:
            result.append(f"ext:{ext}")
        result.append(row["status_tag"])
        if row["is_duplicate"]:
            result.append("rec:duplicate")
        if row["is_empty"]:
            result.append("rec:empty")
        if row["is_temp"]:
            result.append("rec:temp")
        if row["is_cache"]:
            result.append("rec:cache")
        return [tag for tag in result if tag in tag_ids]

    def score(row: Dict[str, Any]) -> Tuple[int, int, float, float]:
        return (
            1 if row["content_status"] == "indexed" else 0,
            0 if row["top_dir"] in CACHE_DIRS else 1,
            float(row["mtime"] or 0),
            math.log1p(max(0, int(row["size"] or 0))),
        )

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["category"]].append(row)
    per_category = max(1, max_files // max(1, len(grouped)))
    selected: List[Dict[str, Any]] = []
    seen: set = set()
    for category, items in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True):
        for row in sorted(items, key=score, reverse=True)[:per_category]:
            if len(selected) >= max_files:
                break
            path = row["rel_path"]
            if path in seen:
                continue
            seen.add(path)
            selected.append(row)
        if len(selected) >= max_files:
            break
    # 补足 + 保证项目文件有代表
    for row in sorted(rows, key=score, reverse=True):
        if len(selected) >= max_files:
            break
        if row["rel_path"] in seen:
            continue
        seen.add(row["rel_path"])
        selected.append(row)
    project_extra = [row for row in rows if row["project"] and row["rel_path"] not in seen]
    for row in sorted(project_extra, key=score, reverse=True)[:min(60, max_files)]:
        if len(selected) >= max_files:
            break
        seen.add(row["rel_path"])
        selected.append(row)

    emit(62, "打标签")
    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    root = {
        "id": "root", "label": "本地知识库", "kind": "root", "color": "#e2e8f0",
        "x": 0.0, "y": 0.0, "z": 0.0, "radius": 20, "count": int(summary_row["files"] or 0),
    }
    nodes.append(root)

    golden = math.pi * (3 - math.sqrt(5))
    tag_positions: Dict[str, Tuple[float, float, float]] = {}
    tag_parent: Dict[str, str] = {}
    ordered_tags = sorted(tags, key=lambda item: (item["tagKind"], item["label"]))
    category_tags = [tag for tag in ordered_tags if tag["tagKind"] == "category"]
    # 整体融合球体（2026-11-05 村长要求）：所有节点（分类/子标签/文件）不再围绕父节点
    # 聚团成"板块"，而是用全局黄金角序列均匀散布在整个球体体积内；
    # 不画板块间连线，parent 字段保留供前端聚焦/颜色区分，root 仍在球心
    sphere_radius = 640.0
    fusion_index = [0]

    def fusion_pos() -> Tuple[float, float, float]:
        i = fusion_index[0]
        fusion_index[0] += 1
        theta = golden * i
        phi = math.acos(1 - 2 * ((i * 0.61803398875) % 1.0))
        radius = sphere_radius * (0.2 + 0.8 * ((i * 0.75487766625) % 1.0))
        return (
            math.sin(phi) * math.cos(theta) * radius,
            math.cos(phi) * radius,
            math.sin(phi) * math.sin(theta) * radius,
        )

    for tag in category_tags:
        x, y, z = fusion_pos()
        tag["x"], tag["y"], tag["z"] = x, y, z
        tag["radius"] = 7 + min(12, math.log10(tag["count"] + 1) * 3.4)
        tag_positions[tag["id"]] = (x, y, z)
        tag_parent[tag["id"]] = "root"
        tag["parent"] = "root"
        nodes.append(tag)

    def dominant(counts: Counter, fallback: str = "其他") -> str:
        return counts.most_common(1)[0][0] if counts else fallback

    def place_satellite(tag: Dict[str, Any], parent: str, distance: float) -> None:
        x, y, z = fusion_pos()
        tag["x"], tag["y"], tag["z"] = x, y, z
        tag["radius"] = 7 + min(12, math.log10(tag["count"] + 1) * 3.4)
        tag_positions[tag["id"]] = (x, y, z)
        tag_parent[tag["id"]] = parent
        tag["parent"] = parent
        nodes.append(tag)

    for tag in ordered_tags:
        kind = tag["tagKind"]
        if kind == "category":
            continue
        if kind == "ext":
            parent = f"cat:{category_of(str(tag['label']))}"
            distance = 165.0
        elif kind == "dir":
            label = str(tag["label"])
            parent = "cat:其他" if label == "工具/缓存" else f"cat:{dominant(dir_category_counts.get(label, Counter()))}"
            distance = 175.0
        elif kind == "project":
            parent = f"cat:{dominant(project_category_counts.get(str(tag['label']), Counter()))}"
            distance = 185.0
        else:
            parent = "root"
            distance = 210.0 + (sum(ord(ch) for ch in tag["id"]) % 5) * 12
        if parent not in tag_positions:
            parent = "root"
        place_satellite(tag, parent, distance)

    emit(70, "融合球体")
    def file_position(primary: str, path: str) -> Tuple[float, float, float]:
        return fusion_pos()

    for index, row in enumerate(selected):
        file_tags = tags_for(row)
        primary = f"cat:{row['category']}" if f"cat:{row['category']}" in tag_ids else (file_tags[0] if file_tags else "cat:其他")
        x, y, z = file_position(primary, str(row["rel_path"]))
        node_id = f"file:{index}"
        size = int(row["size"] or 0)
        glyph, word = glyphs_of(str(row["name"]))
        nodes.append({
            "id": node_id,
            "label": str(row["name"]),
            "glyph": glyph,
            "word": word,
            "kind": "file",
            "path": str(row["rel_path"]),
            "tags": file_tags,
            "primary": primary,
            "status": str(row["content_status"] or ""),
            "size": human_size(size),
            "bytes": size,
            "mtime": float(row["mtime"] or 0),
            "color": CATEGORY_COLORS.get(row["category"], "#94a3b8"),
            "x": x, "y": y, "z": z,
            "radius": 2.4 + min(3.6, math.log10(size + 1) * 0.45),
        })
        # 融合球体（2026-11-05）：不画任何节点间连线，文件与标签的关联只保留在字段里

    emit(88, "生成星点")
    # 语义连线（2026-11-09）：展示中的文件按向量余弦两两连边（阈值 0.80、每点最多 2 条），
    # 知识"簇"肉眼可见；无 numpy/无向量时静默跳过，不影响基础图
    try:
        links.extend(_semantic_edges(db_path, selected))
    except Exception:  # noqa: BLE001
        pass
    summary = {
        "files": int(summary_row["files"] or 0),        "bytes": int(summary_row["bytes"] or 0),
        "bytes_text": human_size(int(summary_row["bytes"] or 0)),
        "indexed": int(summary_row["indexed"] or 0),
        "unsupported": int(summary_row["unsupported"] or 0),
        "too_large": int(summary_row["too_large"] or 0),
        "errors": int(summary_row["errors"] or 0),
        "tags": len(tags),
        "nodes": len(nodes),
        "links": len(links),
        "shown_files": len(selected),
    }
    if row_sink is not None:
        row_sink.extend(rows)
    emit(98, "整理图数据")
    return {
        "version": int(time.time() * 1000),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "tags": tags,
        "recommendations": recommendations,
        "organize_suggestions": organize_suggestions,
        "nodes": nodes,
        "links": links,
    }


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem} {index}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}-{int(time.time())}{suffix}")


def trash_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / ".Trash"
    if os.name == "nt":
        return Path.home() / "$RECYCLE.BIN"
    return Path.home() / ".local" / "share" / "Trash" / "files"


def move_to_trash(path: Path) -> str:
    """把文件移到系统废纸篓，不直接删除。"""
    if os.name == "nt" and sys.platform != "darwin":
        script = (
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{str(path)}',"
            "'OnlyErrorDialogs','SendToRecycleBin')"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True,
                       capture_output=True, text=True)
        return str(path)
    root = trash_root()
    root.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(root / path.name)
    shutil.move(str(path), str(destination))
    return str(destination)


class GraphState:
    def __init__(self, kb: Path, db_path: Path, *, max_files: int, interval: float) -> None:
        self.kb = kb
        self.db_path = db_path
        self.max_files = max_files
        self.interval = interval
        self.lock = threading.Lock()
        self.graph: Optional[Dict[str, Any]] = None
        self.signature: Optional[Tuple[int, int]] = None
        self.indexing = False
        self.index_proc: Optional[subprocess.Popen] = None
        self.last_error = ""
        self.search_rows: Optional[List[Dict[str, Any]]] = None
        # 全库向量缓存（档案卡"相似文件"用）：(数量, 最大id) 变化时重建
        self._embed_index: Optional[Tuple[int, int]] = None
        self._embed_ids: List[int] = []
        self._embed_paths: List[str] = []
        self._embed_matrix: Any = None
        self._rebuilding = False
        self._rebuild_pending = False
        self.build_lock = threading.Lock()
        self._last_rebuild_at = 0.0
        self.progress: Dict[str, Any] = {"active": False, "percent": 0, "message": ""}

    def kb_python(self) -> str:
        candidate = self.kb / ".venv" / "bin" / "python"
        return str(candidate) if candidate.is_file() else sys.executable

    def _set_progress(self, active: bool, percent: int, message: str) -> None:
        with self.lock:
            self.progress = {"active": active, "percent": max(0, min(100, int(percent))),
                             "message": message}

    def rebuild(self) -> None:
        with self.build_lock:
            self._last_rebuild_at = time.time()
            self._set_progress(True, 0, "准备读取索引")
            try:
                sink: List[Dict[str, Any]] = []
                graph = build_graph(self.db_path, max_files=self.max_files, row_sink=sink,
                                    progress=lambda pct, msg: self._set_progress(True, pct, msg))
                with self.lock:
                    self.graph = graph
                    stat = self.db_path.stat()
                    self.signature = (stat.st_mtime_ns, stat.st_size)
                    self.search_rows = sink
                    self.last_error = ""
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    self.last_error = str(exc)
            finally:
                self._set_progress(False, 100, "完成")

    def get_graph(self) -> Dict[str, Any]:
        """返回图数据。有缓存立即返回（重建期间给旧版，前端按 version 轮询会再拉新）；
        仅在首次无缓存时才同步构建，避免前端长时间挂在请求上。"""
        with self.lock:
            if self.graph is not None:
                return self.graph
        with self.build_lock:
            with self.lock:
                if self.graph is not None:
                    return self.graph
            self._set_progress(True, 0, "准备读取索引")
            try:
                sink: List[Dict[str, Any]] = []
                graph = build_graph(self.db_path, max_files=self.max_files, row_sink=sink,
                                    progress=lambda pct, msg: self._set_progress(True, pct, msg))
                with self.lock:
                    self.graph = graph
                    stat = self.db_path.stat()
                    self.signature = (stat.st_mtime_ns, stat.st_size)
                    self.search_rows = sink
            finally:
                self._set_progress(False, 100, "完成")
            return graph

    def status(self) -> Dict[str, Any]:
        from .auto_embed import embedding_brief

        with self.lock:
            graph = self.graph or {}
            return {
                "version": graph.get("version", 0),
                "generated_at": graph.get("generated_at", ""),
                "summary": graph.get("summary", {}),
                "indexing": self.indexing,
                "progress": dict(self.progress),
                "embedding": embedding_brief(self.db_path),
                "error": self.last_error,
            }

    def assistant_profile(self) -> Dict[str, str]:
        from .config import load_config

        assistant = (load_config().get("assistant") or {})
        return {
            "name": str(assistant.get("name") or "小助手"),
            "persona": str(assistant.get("persona") or "你的专属小助手，说话简短、自然"),
        }

    def save_assistant(self, name: str, persona: str) -> Dict[str, Any]:
        name = str(name or "").strip()[:40] or "小助手"
        persona = str(persona or "").strip()[:200] or "你的专属小助手，说话简短、自然"
        path = HOME / "config" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {}
        try:
            if path.is_file():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
        data["assistant"] = {"name": name, "persona": persona}
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return {"ok": True, "assistant": {"name": name, "persona": persona}}

    def selfcheck(self) -> Dict[str, Any]:
        path = self.kb / "logs" / "selfcheck.log"
        if not path.is_file():
            return {"available": False, "message": "还没有自检记录"}
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 65536))
                lines = handle.read().decode("utf-8", "replace").splitlines()
            line = next((item for item in reversed(lines) if item.strip()), "")
            data = json.loads(line)
        except (OSError, ValueError, json.JSONDecodeError):
            return {"available": False, "message": "自检日志读取失败"}
        checks = data.get("checks") or []
        index = data.get("index") or {}
        services = data.get("services") or {}
        warnings = [
            {"name": item.get("name", ""), "status": item.get("status", ""),
             "message": item.get("message", "")}
            for item in checks if item.get("status") != "ok"
        ]
        return {
            "available": True,
            "time": data.get("time", ""),
            "status": data.get("status", "ok"),
            "files": int(index.get("files") or 0),
            "errors": int(index.get("errors") or 0),
            "services": len(services.get("alive") or []) + (1 if services.get("map_alive") else 0),
            "warnings": warnings,
        }

    def _embed_index_matrix(self) -> None:
        """懒加载全库 int8 向量（int16 矩阵）+ file_id/path 列表，带失效签名。"""
        import numpy as np

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            stat = conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(MAX(file_id),0) AS m FROM file_embeddings"
            ).fetchone()
            signature = (int(stat["c"] or 0), int(stat["m"] or 0))
            if self._embed_index == signature and self._embed_matrix is not None:
                return
            rows = conn.execute(
                """
                SELECT e.file_id AS fid, f.rel_path AS path, e.data AS data
                FROM file_embeddings e JOIN files f ON f.id = e.file_id
                ORDER BY e.file_id
                """
            ).fetchall()
        finally:
            conn.close()
        dim = len(rows[0]["data"]) if rows else 0
        matrix = np.empty((len(rows), dim), dtype=np.int16) if rows else np.empty((0, 0), dtype=np.int16)
        ids: List[int] = []
        paths: List[str] = []
        for i, row in enumerate(rows):
            ids.append(int(row["fid"]))
            paths.append(str(row["path"]))
            matrix[i] = np.frombuffer(row["data"], dtype=np.int8)
        self._embed_ids = ids
        self._embed_paths = paths
        self._embed_matrix = matrix
        self._embed_index = signature

    def file_info(self, path: str) -> Dict[str, Any]:
        """档案卡：文件元数据 + 正文预览 + 语义相似文件 top5。"""
        import numpy as np

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT id, rel_path, name, ext, size, mtime, content_status, content FROM files WHERE rel_path = ?",
                (path,),
            ).fetchone()
            if row is None:
                return {"error": "文件不在索引里"}
            fid = int(row["id"])
            vector = conn.execute(
                "SELECT data FROM file_embeddings WHERE file_id = ?", (fid,)
            ).fetchone()
        finally:
            conn.close()

        def mtime_text(value: float) -> str:
            if not value:
                return ""
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(value)))

        preview = str(row["content"] or "")[:600]
        info: Dict[str, Any] = {
            "name": str(row["name"] or ""),
            "path": str(row["rel_path"] or ""),
            "ext": str(row["ext"] or ""),
            "size": int(row["size"] or 0),
            "size_text": human_size(int(row["size"] or 0)),
            "mtime": float(row["mtime"] or 0),
            "mtime_text": mtime_text(float(row["mtime"] or 0)),
            "status": str(row["content_status"] or ""),
            "preview": preview,
            "similar": [],
        }
        if vector is not None and len(vector["data"]) > 0:
            try:
                self._embed_index_matrix()
                ids = self._embed_ids
                if fid in ids:
                    # q 升到 int32 触发类型提升，避免 int16 点积溢出
                    q = np.frombuffer(vector["data"], dtype=np.int8).astype(np.int32)
                    scores = self._embed_matrix @ q
                    order = np.argsort(-scores)[:8]
                    for idx in order:
                        other_id = ids[int(idx)]
                        if other_id == fid:
                            continue
                        score = round(int(scores[int(idx)]) / (127.0 * 127.0), 3)
                        if score < 0.55:
                            continue
                        info["similar"].append({
                            "path": self._embed_paths[int(idx)],
                            "name": Path(self._embed_paths[int(idx)]).name,
                            "score": score,
                        })
                        if len(info["similar"]) >= 5:
                            break
            except Exception:  # noqa: BLE001
                pass
        return info

    def _running_index_pid(self) -> Optional[int]:
        try:
            pid = int((self.kb / "data" / "index.pid").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid

    def _clear_index_pid(self) -> None:
        try:
            (self.kb / "data" / "index.pid").unlink()
        except OSError:
            pass

    def start_index(self) -> Dict[str, Any]:
        if self.indexing:
            return {"ok": False, "message": "已经在更新索引"}
        from .index_lock import IndexLock

        probe = IndexLock(self.kb / "data" / "index.lock")
        if not probe.acquire():
            holder = probe.holder_pid()
            return {"ok": False, "message": f"已经在更新索引（PID {holder or '未知'}）"}
        probe.release()
        running = self._running_index_pid()
        if running:
            return {"ok": False, "message": f"已经在更新索引（PID {running}）"}
        try:
            self.index_proc = subprocess.Popen(
                [self.kb_python(), "-m", "kb", "index", "--quiet"],
                cwd=str(self.kb),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                (self.kb / "data" / "index.pid").write_text(str(self.index_proc.pid), encoding="utf-8")
            except OSError:
                pass
            self.indexing = True
            return {"ok": True, "message": "已开始更新索引"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}

    def watch(self) -> None:
        while True:
            time.sleep(self.interval)
            if self.index_proc is not None:
                if self.index_proc.poll() is not None:
                    self.indexing = False
                    self.index_proc = None
                    self._clear_index_pid()
                    self.rebuild()
                continue
            try:
                stat = self.db_path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
            with self.lock:
                changed = signature != self.signature
            # 重建节流：嵌入/索引会频繁改 kb.db，两次重建至少间隔 120s，避免 CPU 空转
            if changed and not self.indexing and time.time() - self._last_rebuild_at >= 120:
                self.rebuild()

    def run_kb(self, args: List[str], *, timeout: float) -> Dict[str, Any]:
        try:
            result = subprocess.run(
                [self.kb_python(), "-m", "kb", *args],
                cwd=str(self.kb),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"error": "命令超时"}
        if result.returncode != 0:
            return {"error": (result.stderr or result.stdout or "命令失败").strip()[:1000]}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"error": result.stdout[:1000]}

    def _search_cache(self) -> List[Dict[str, Any]]:
        with self.lock:
            if self.search_rows is not None:
                return self.search_rows
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(
                "SELECT rel_path, name, ext, size, mtime, content_status, sha256 FROM files"
            )]
        finally:
            conn.close()
        with self.lock:
            self.search_rows = rows
        return rows

    def _fuzzy_search(self, query: str) -> List[Dict[str, Any]]:
        q = query.strip().lower()
        if len(q) < 2:
            return []
        rows = self._search_cache()
        qchars = {ch for ch in q if not ch.isspace()}
        candidates: List[tuple] = []
        exact: List[Dict[str, Any]] = []
        for row in rows:
            name = str(row["name"] or "")
            rel = str(row["rel_path"] or "")
            haystack = (name + " " + rel).lower()
            if q in haystack:
                exact.append(row)
                continue
            parts = [part for part in rel.split("/") if part]
            if len(q) >= 4 and not any(abs(len(part) - len(q)) <= 2 for part in parts) and abs(len(name) - len(q)) > 2:
                continue
            overlap = len(qchars & set(haystack)) / max(1, len(qchars))
            if overlap < 0.35:
                continue
            candidates.append((overlap, row))
        candidates.sort(key=lambda item: item[0], reverse=True)

        def result(row: Dict[str, Any], reason: str) -> Dict[str, Any]:
            return {
                "path": row["rel_path"],
                "name": row["name"],
                "size": int(row["size"] or 0),
                "size_text": human_size(int(row["size"] or 0)),
                "content_status": row["content_status"],
                "snippet": reason,
            }

        results = [result(row, "名称/路径包含关键词") for row in exact[:20]]
        scored: List[tuple] = []
        threshold = 0.60 if len(q) <= 3 else 0.40
        for overlap, row in candidates[:1600]:
            name = str(row["name"] or "").lower()
            rel = str(row["rel_path"] or "").lower()
            if len(q) <= 3:
                ratio = overlap + (0.15 if name.startswith(q[:1]) else 0.0)
            else:
                name_ratio = difflib.SequenceMatcher(None, q, name).ratio()
                path_ratio = max(
                    (difflib.SequenceMatcher(None, q, part).ratio() for part in rel.split("/") if part),
                    default=0.0,
                )
                ratio = max(name_ratio, path_ratio * 0.95)
            if ratio >= threshold:
                scored.append((ratio, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        seen = {str(item["path"]) for item in results}
        for ratio, row in scored[:20]:
            if str(row["rel_path"]) in seen:
                continue
            seen.add(str(row["rel_path"]))
            results.append(result(row, f"模糊匹配 {ratio:.2f}"))
        return results[:30]

    def search(self, query: str) -> Any:
        exact = self.run_kb(["search", query, "--limit", "30", "--json"], timeout=2)
        exact_items = exact if isinstance(exact, list) else []
        if len(exact_items) >= 12:
            return exact_items
        fuzzy_items = self._fuzzy_search(query)
        combined: List[Dict[str, Any]] = []
        paths: List[str] = []
        for item in exact_items + fuzzy_items:
            path = str(item.get("path") or item.get("name") or "").replace("\\", "/").lower()
            if any(path == seen or path.endswith("/" + seen) or seen.endswith("/" + path) for seen in paths):
                continue
            paths.append(path)
            combined.append(item)
        return combined[:30]

    def ask(self, query: str) -> Dict[str, Any]:
        from .db import connect as db_connect
        from .search import ask as search_ask

        conn = db_connect(self.db_path)
        try:
            return search_ask(conn, query, limit=5)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        finally:
            conn.close()

    def query(self, text: str) -> Dict[str, Any]:
        from .search import is_identity_question

        if is_identity_question(text):
            answer = self.ask(text)
            return {
                "mode": "answer",
                "query": text,
                "results": [],
                "answer": answer.get("answer", "我是你的专属小助手，可以帮你找文件、整理资料。"),
                "model": "",
                "ask_mode": answer.get("mode", "persona"),
                "sources": [],
            }
        results = self.search(text)
        wants_answer = bool(QUESTION_HINTS.search(text)) or len(results) == 0
        if not wants_answer:
            return {"mode": "search", "query": text, "results": results}
        answer = self.ask(text)
        if answer.get("error"):
            return {"mode": "search", "query": text, "results": results, "error": answer["error"]}
        if answer.get("mode") == "persona":
            return {
                "mode": "answer",
                "query": text,
                "results": [],
                "answer": answer.get("answer", ""),
                "model": "",
                "ask_mode": "persona",
                "sources": [],
            }
        return {
            "mode": "answer",
            "query": text,
            "results": results[:8],
            "answer": answer.get("answer", ""),
            "model": answer.get("model", ""),
            "ask_mode": answer.get("mode", ""),
            "sources": answer.get("sources", []),
        }

    def record(self, path: str) -> Dict[str, Any]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT rel_path, name, ext, size, mtime, content_status, error FROM files WHERE rel_path=? OR path=? LIMIT 1",
                (path, str((self.kb / path).resolve())),
            ).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    def request_rebuild(self) -> None:
        with self.lock:
            if self._rebuilding:
                self._rebuild_pending = True
                return
            self._rebuilding = True

        def run() -> None:
            try:
                while True:
                    self.rebuild()
                    with self.lock:
                        if not self._rebuild_pending:
                            break
                        self._rebuild_pending = False
            finally:
                with self.lock:
                    self._rebuilding = False

        threading.Thread(target=run, daemon=True).start()

    def _file_row(self, conn: sqlite3.Connection, raw: str) -> Optional[Dict[str, Any]]:
        rel = str(raw or "").strip().replace("\\", "/").lstrip("/")
        row = conn.execute(
            "SELECT id, path, rel_path, name, size, ext, sha256 FROM files "
            "WHERE rel_path=? OR path=? LIMIT 1",
            (rel, str(raw)),
        ).fetchone()
        return dict(row) if row else None

    def _duplicate_counts(self, conn: Optional[sqlite3.Connection] = None) -> Counter:
        with self.lock:
            cached = self.search_rows
        if cached:
            counts: Counter = Counter()
            for row in cached:
                digest = str(row.get("sha256") or "")
                if digest and int(row.get("size") or 0) > 0:
                    counts[digest] += 1
            if counts:
                return counts
        owns = conn is None
        if conn is None:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT sha256, COUNT(*) AS total FROM files "
                "WHERE size > 0 AND sha256 != '' GROUP BY sha256 HAVING total > 1"
            ).fetchall()
        finally:
            if owns:
                conn.close()
        return Counter({str(row[0]): int(row[1]) for row in rows})

    def _trash_reason(self, row: Dict[str, Any], duplicates: Counter) -> str:
        name = str(row.get("name") or "").lower()
        if int(row.get("size") or 0) == 0:
            return "空文件"
        if (name in {".ds_store", "thumbs.db", "desktop.ini"}
                or name.startswith("~$")
                or name.endswith((".tmp", ".temp", ".part", ".crdownload", ".download"))):
            return "临时文件"
        digest = str(row.get("sha256") or "")
        if digest and duplicates.get(digest, 0) > 1:
            return "重复文件"
        return ""

    def trash(self, paths: List[str], *, confirm: bool = False) -> Dict[str, Any]:
        if not confirm:
            return {"error": "删除需要二次确认"}
        cleaned = [str(item).strip() for item in (paths or []) if str(item).strip()]
        if not cleaned:
            return {"error": "没有选择文件"}
        cleaned = cleaned[:200]
        moved: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        duplicates: Optional[Counter] = None
        try:
            for raw in cleaned:
                try:
                    row = self._file_row(conn, raw)
                    if not row:
                        raise FileNotFoundError("索引里没有这个文件")
                    target = ensure_allowed_path(conn, Path(str(row["path"])).expanduser())
                    if target.is_dir():
                        raise IsADirectoryError("目录不在这里删除")
                    if not target.is_file():
                        raise FileNotFoundError("文件已不存在")
                    name = str(row.get("name") or "").lower()
                    if int(row.get("size") or 0) == 0 or (
                        name in {".ds_store", "thumbs.db", "desktop.ini"}
                        or name.startswith("~$")
                        or name.endswith((".tmp", ".temp", ".part", ".crdownload", ".download"))
                    ):
                        reason = self._trash_reason(row, Counter())
                    else:
                        if duplicates is None:
                            duplicates = self._duplicate_counts(conn)
                        reason = self._trash_reason(row, duplicates)
                    if not reason:
                        raise PermissionError("只允许删除重复 / 空 / 临时文件")
                    destination = move_to_trash(target)
                    delete_file_by_id(conn, int(row["id"]))
                    moved.append({"path": row["rel_path"], "trash": destination, "reason": reason})
                except Exception as exc:  # noqa: BLE001
                    failed.append({"path": raw, "error": str(exc)})
        finally:
            conn.close()
        if moved:
            self.request_rebuild()
        return {
            "ok": not failed,
            "moved": moved,
            "failed": failed,
            "moved_count": len(moved),
            "failed_count": len(failed),
        }

    def reveal(self, path: str) -> Dict[str, Any]:
        raw = str(path or "").strip()
        if not raw:
            return {"error": "缺少路径"}
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = self._file_row(conn, raw)
            candidates: List[Path] = []
            if row:
                candidates.append(Path(str(row["path"])).expanduser())
            else:
                relative = Path(raw).expanduser()
                if relative.is_absolute():
                    candidates.append(relative)
                else:
                    candidates.extend([Path.home() / raw, self.kb / raw])
            target = None
            for candidate in candidates:
                try:
                    allowed = ensure_allowed_path(conn, candidate)
                except PermissionError:
                    continue
                if allowed.exists():
                    target = allowed
                    break
                if target is None:
                    target = allowed
            if target is None:
                return {"error": f"路径不在索引范围内：{raw}"}
        finally:
            conn.close()
        if not target.exists():
            return {"error": f"文件不存在：{target}"}
        try:
            if sys.platform == "darwin":
                if target.is_dir():
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["open", "-R", str(target)])
            elif os.name == "nt":
                if target.is_dir():
                    os.startfile(str(target))  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["explorer", f"/select,{target}"])
            else:
                subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        return {"ok": True, "path": str(target), "dir": str(target.parent)}

    def organize(self, *, action: str, path: str = "", strategy: str = "image",
                 recursive: bool = False, plan_id: str = "") -> Dict[str, Any]:
        action = str(action or "").strip()
        if action == "plan":
            target = str(path or "").strip()
            if not target:
                return {"error": "请填写要整理的目录"}
            args = ["organize", target, "--strategy", strategy, "--plan", "--json"]
            if recursive:
                args.append("--recursive")
            data = self.run_kb(args, timeout=900)
            if data.get("error"):
                return data
            operations = data.get("operations") or []
            categories = Counter(str(op.get("category") or "") for op in operations)
            return {
                "ok": True,
                "plan_id": data.get("plan_id"),
                "target": data.get("target") or target,
                "strategy": data.get("strategy") or strategy,
                "count": len(operations),
                "categories": [{"name": name, "count": count} for name, count in categories.most_common()],
                "operations": operations[:40],
                "truncated": len(operations) > 40,
            }
        if action == "apply":
            if not plan_id:
                return {"error": "缺少计划 ID"}
            data = self.run_kb(["organize", "--apply", plan_id, "--yes"], timeout=900)
            if not data.get("error"):
                self.request_rebuild()
            return data
        if action == "undo":
            if not plan_id:
                return {"error": "缺少计划 ID"}
            data = self.run_kb(["organize", "--undo", plan_id], timeout=900)
            if not data.get("error"):
                self.request_rebuild()
            return data
        return {"error": "未知操作"}


class Handler(BaseHTTPRequestHandler):
    server_version = "kb-map/1.0"
    protocol_version = "HTTP/1.0"
    state: GraphState
    web_dir: Path

    def _send(self, data: bytes, status: int = 200, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), status)

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 1_000_000:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path in ("/", "/index.html"):
            page = self.web_dir / "index.html"
            if not page.is_file():
                self._send(b"web/index.html missing", 404, "text/plain; charset=utf-8")
                return
            self._send(page.read_bytes(), 200, "text/html; charset=utf-8")
            return
        if parsed.path.startswith(("/css/", "/js/")):
            target = (self.web_dir / parsed.path.lstrip("/")).resolve()
            web_root = self.web_dir.resolve()
            if str(target).startswith(str(web_root)) and target.is_file():
                ctype = ("text/css; charset=utf-8" if target.suffix == ".css"
                         else "application/javascript; charset=utf-8")
                self._send(target.read_bytes(), 200, ctype)
            else:
                self._send(b"not found", 404, "text/plain; charset=utf-8")
            return
        if parsed.path == "/api/file_info":
            path = (params.get("path") or [""])[0].strip()
            if not path:
                self._json({"error": "缺少 path 参数"}, 400)
                return
            try:
                self._json(self.state.file_info(path))
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 500)
            return
        if parsed.path == "/api/graph":
            try:
                self._json(self.state.get_graph())
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 500)
            return
        if parsed.path == "/api/status":
            self._json(self.state.status())
            return
        if parsed.path == "/api/selfcheck":
            self._json(self.state.selfcheck())
            return
        if parsed.path == "/api/assistant":
            self._json(self.state.assistant_profile())
            return
        if parsed.path == "/api/search":
            query = (params.get("q") or [""])[0].strip()
            if not query:
                self._json([])
                return
            self._json(self.state.search(query))
            return
        if parsed.path == "/api/query":
            query = (params.get("q") or [""])[0].strip()
            if not query:
                self._json({"error": "缺少问题"})
                return
            try:
                self._json(self.state.query(query))
            except subprocess.TimeoutExpired:
                self._json({"error": "查询超时（本地模型可能还在加载）"}, 504)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 500)
            return
        if parsed.path == "/api/ask":
            query = (params.get("q") or [""])[0].strip()
            if not query:
                self._json({"error": "缺少问题"})
                return
            try:
                self._json(self.state.ask(query))
            except subprocess.TimeoutExpired:
                self._json({"error": "问答超时（本地模型可能还在加载）"}, 504)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 500)
            return
        if parsed.path == "/api/record":
            path = (params.get("path") or [""])[0].strip()
            self._json(self.state.record(path) if path else {})
            return
        if parsed.path == "/api/reveal":
            path = (params.get("path") or [""])[0].strip()
            self._json(self.state.reveal(path) if path else {"error": "缺少路径"})
            return
        if parsed.path == "/favicon.ico":
            self._send(b"", 204, "image/x-icon")
            return
        self._send(b"Not found", 404, "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        route = urllib.parse.urlparse(self.path).path
        if route == "/api/refresh":
            self._json(self.state.start_index())
            return
        if route == "/api/trash":
            payload = self._read_json()
            paths = payload.get("paths") or []
            if not isinstance(paths, list):
                self._json({"error": "paths 必须是数组"}, 400)
                return
            self._json(self.state.trash(paths, confirm=bool(payload.get("confirm"))))
            return
        if route == "/api/organize":
            payload = self._read_json()
            self._json(self.state.organize(
                action=str(payload.get("action") or ""),
                path=str(payload.get("path") or ""),
                strategy=str(payload.get("strategy") or "image"),
                recursive=bool(payload.get("recursive")),
                plan_id=str(payload.get("plan_id") or ""),
            ))
            return
        if route == "/api/assistant":
            payload = self._read_json()
            self._json(self.state.save_assistant(
                str(payload.get("name") or ""),
                str(payload.get("persona") or ""),
            ))
            return
        self._send(b"Not found", 404, "text/plain; charset=utf-8")

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[kb-map] {self.client_address[0]} {fmt % args}", flush=True)


def run_map(args: Any) -> int:
    kb = Path(args.kb).expanduser().resolve()
    db_path = kb / "data" / "kb.db"
    if not db_path.is_file():
        print(f"数据库不存在：{db_path}")
        return 1
    state = GraphState(kb, db_path, max_files=max(args.max_files, 50), interval=max(args.interval, 2))
    if args.index_on_start:
        print(state.start_index()["message"], flush=True)
    threading.Thread(target=state.watch, daemon=True).start()
    from .auto_embed import MIN_START_GAP, maybe_auto_embed
    maybe_auto_embed(kb, reason="map-start", gap_threshold=MIN_START_GAP)

    Handler.state = state
    Handler.web_dir = Path(__file__).resolve().parent / "map_web"
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"启动失败（{args.host}:{args.port}）：{exc}")
        return 1
    server.daemon_threads = True
    pid_path = kb / "logs" / "map.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    url = f"http://{args.host}:{args.port}/"
    print(f"3D 脑图：{url}", flush=True)
    print(f"PID {os.getpid()}，停止：kill {os.getpid()}", flush=True)
    print("按 Ctrl+C 停止。", flush=True)
    print("页面会自动跟随 kb.db 变化；点“更新索引”可立即触发一次 kb index。", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
        try:
            if pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink()
        except OSError:
            pass
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="本地知识库 3D 脑图 + 助手")
    parser.add_argument("--kb", default=str(DEFAULT_KB), help="personal-kb 目录")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--max-files", type=int, default=500)
    parser.add_argument("--interval", type=float, default=60.0, help="检查 kb.db 变化的秒数")
    parser.add_argument("--index-on-start", action="store_true", help="启动时触发一次增量索引")
    parser.add_argument("--open", action="store_true", help="启动后用默认浏览器打开")
    return run_map(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
