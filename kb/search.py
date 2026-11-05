from __future__ import annotations

import html
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import extract, llm
from .config import load_config
from .db import (
    ensure_allowed_path,
    get_file_by_path,
    make_fts_query,
    root_for_path,
    search_like,
    search_rows,
)

try:
    import numpy as np
except ImportError:  # 无 numpy 时自动退化为纯 FTS5 检索
    np = None  # type: ignore[assignment]


STOPWORDS = (
    "请问", "帮我", "帮忙", "一下", "是什么", "怎么样", "怎么", "如何",
    "为什么", "哪些", "哪个", "相关", "关于", "本地", "电脑", "文件",
    "资料", "内容", "可以", "能否", "是否", "我想", "我要", "查一下",
    "找一下", "看一下", "的", "了", "吗", "呢", "啊", "呀",
)

_OLLAMA_CACHE: dict[str, tuple[float, str]] = {}

_IDENTITY_PATTERN = re.compile(
    r"^\s*(你是谁|你叫什么|你的名字|介绍一下你|介绍下你|你能做什么|你会什么|who\s+are\s+you)",
    re.I,
)


def _assistant_profile(config: dict[str, Any]) -> tuple[str, str]:
    assistant = config.get("assistant") or {}
    name = str(assistant.get("name") or "小助手").strip() or "小助手"
    persona = str(assistant.get("persona") or "你的专属小助手，说话简短、自然").strip()
    return name, persona


def is_identity_question(text: str) -> bool:
    return bool(_IDENTITY_PATTERN.match(str(text or "")))


def _fmt_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{size}B"


def _looks_like_path(query: str) -> bool:
    text = query.strip()
    if not text:
        return False
    return "/" in text or "\\" in text or bool(Path(text).suffix)


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_path(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    ext: str = "",
    root_id: int | None = None,
) -> list[sqlite3.Row]:
    """按文件名/相对路径优先匹配，适合“发我 docs/xxx.md”这类请求。"""
    normalized = query.strip().replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.strip("/")
    if not normalized:
        return []
    name = Path(normalized).name
    candidates = [normalized]
    if name and name != normalized:
        candidates.append(name)

    clauses = []
    params: list[Any] = []
    for candidate in candidates:
        pattern = f"%{_like_escape(candidate)}%"
        clauses.append("(f.rel_path LIKE ? ESCAPE '\\' OR f.name LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern])

    sql = f"""
        SELECT f.*, 0.0 AS rank, substr(f.content, 1, 240) AS snippet
        FROM files f
        WHERE ({' OR '.join(clauses)})
    """
    if ext:
        sql += " AND f.ext = ?"
        params.append(ext.lower())
    if root_id is not None:
        sql += " AND f.root_id = ?"
        params.append(root_id)
    sql += " ORDER BY f.mtime DESC LIMIT ?"
    params.append(max(limit * 5, 50))
    rows = conn.execute(sql, params).fetchall()

    def score(row: sqlite3.Row) -> int:
        rel_path = str(row["rel_path"]).replace("\\", "/")
        file_name = str(row["name"])
        if file_name == name:
            return 0
        if rel_path == normalized or rel_path.endswith("/" + normalized):
            return 1
        if file_name.lower() == name.lower():
            return 2
        return 3

    rows = sorted(rows, key=lambda row: (score(row), -float(row["mtime"])))
    return rows[:limit]


def query_terms(query: str) -> list[str]:
    cleaned = query
    for stopword in STOPWORDS:
        cleaned = cleaned.replace(stopword, " ")
    terms: list[str] = []
    for token in re.split(r"[\s/\\]+", cleaned.strip()):
        token = token.strip().strip(".,;:!?()[]{}<>\"'")
        if not token:
            continue
        terms.append(token)
        stem = Path(token).stem
        if stem and stem != token and len(stem) >= 2:
            terms.append(stem)
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", token):
            terms.append(run)
            if len(run) > 2:
                terms.extend(run[index:index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))


def search_like_terms(
    conn: sqlite3.Connection,
    terms: list[str],
    *,
    limit: int,
    ext: str = "",
    root_id: int | None = None,
) -> list[sqlite3.Row]:
    if not terms:
        return []
    clauses = " OR ".join(
        "(f.name LIKE ? OR f.rel_path LIKE ? OR f.content LIKE ?)" for _ in terms
    )
    params: list[Any] = []
    for term in terms:
        like = f"%{term}%"
        params.extend([like, like, like])
    sql = f"""
        SELECT f.*, 0.0 AS rank, substr(f.content, 1, 240) AS snippet
        FROM files f
        WHERE ({clauses})
    """
    if ext:
        sql += " AND f.ext = ?"
        params.append(ext.lower())
    if root_id is not None:
        sql += " AND f.root_id = ?"
        params.append(root_id)
    sql += " LIMIT 500"
    rows = conn.execute(sql, params).fetchall()
    scored = []
    min_score = 2 if len(terms) >= 2 else 1
    for row in rows:
        haystack = f"{row['name']} {row['rel_path']} {row['content']}"
        score = sum(1 for term in terms if term in haystack)
        if score < min_score:
            continue
        scored.append((score, float(row["mtime"]), row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in scored[:limit]]


def _row_to_result(row: sqlite3.Row) -> dict[str, Any]:
    snippet = row["snippet"] if "snippet" in row.keys() else ""
    if snippet:
        snippet = re.sub(r"\s+", " ", snippet).strip()
    return {
        "path": row["path"],
        "name": row["name"],
        "rel_path": row["rel_path"],
        "ext": row["ext"],
        "size": int(row["size"]),
        "size_text": _fmt_size(int(row["size"])),
        "mtime": float(row["mtime"]),
        "content_status": row["content_status"],
        "snippet": snippet,
        "score": round(-float(row["rank"]), 4) if "rank" in row.keys() else 0.0,
    }


# ---- 语义向量检索（文件级 int8 向量缓存 + RRF 混合融合） ----
_VECTOR_CACHE: dict[str, Any] = {
    "db": "", "count": -1, "max_id": -1, "ids": None, "mat": None,
}
_QUERY_VEC_CACHE: dict[str, tuple[float, list[float]]] = {}


def _vector_index(conn: sqlite3.Connection) -> tuple[Any, Any] | None:
    """加载全部文件向量到内存（int8，全库约几十 MB），按 (数量, 最大id) 自动失效。"""
    if np is None:
        return None
    try:
        from .db import _database_key

        key = _database_key(conn) or str(id(conn))
    except Exception:  # noqa: BLE001
        key = str(id(conn))
    row = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(MAX(file_id), 0) AS m FROM file_embeddings"
    ).fetchone()
    count, max_id = int(row["c"] or 0), int(row["m"] or 0)
    if count == 0:
        return None
    cache = _VECTOR_CACHE
    if cache["db"] == key and cache["count"] == count and cache["max_id"] == max_id:
        return cache["ids"], cache["mat"]
    ids: list[int] = []
    blobs: list[Any] = []
    for item in conn.execute("SELECT file_id, data FROM file_embeddings ORDER BY file_id"):
        ids.append(int(item["file_id"]))
        blobs.append(np.frombuffer(bytes(item["data"]), dtype=np.int8))
    if not ids:
        return None
    mat = np.stack(blobs)
    cache.update({
        "db": key, "count": count, "max_id": max_id,
        "ids": np.asarray(ids, dtype=np.int64), "mat": mat,
    })
    return cache["ids"], cache["mat"]


def _question_vector(question: str) -> Any | None:
    if np is None:
        return None
    now = time.time()
    cached = _QUERY_VEC_CACHE.get(question)
    if cached and now - cached[0] < 300:
        return np.asarray(cached[1], dtype=np.float32)
    try:
        from . import embedder

        vector = embedder.embed_texts([question])[0]
    except Exception:  # noqa: BLE001
        return None
    if len(_QUERY_VEC_CACHE) > 200:
        _QUERY_VEC_CACHE.clear()
    _QUERY_VEC_CACHE[question] = (now, vector)
    return np.asarray(vector, dtype=np.float32)


def vector_search(
    conn: sqlite3.Connection,
    question: str,
    *,
    top: int = 50,
) -> list[tuple[int, float]]:
    """语义向量召回，返回 [(file_id, cosine)]，按相似度降序。任何不可用自动返回空。"""
    cached_index = _vector_index(conn)
    if cached_index is None:
        return []
    ids, mat = cached_index
    q = _question_vector(question)
    if q is None or q.size == 0:
        return []
    norm = float(np.linalg.norm(q))
    if norm <= 0:
        return []
    q8 = np.clip(np.round(q / norm * 127.0), -127, 127).astype(np.float32)
    scores = (mat.astype(np.float32) @ q8) / (127.0 * 127.0)
    k = min(int(top), int(scores.shape[0]))
    if k <= 0:
        return []
    idx = np.argpartition(-scores, k - 1)[:k]
    idx = idx[np.argsort(-scores[idx], kind="stable")]
    return [(int(ids[i]), float(scores[i])) for i in idx]


def _hybrid_merge(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    vec_hits: list[tuple[int, float]],
    *,
    limit: int,
    ext: str,
    root_id: int | None,
) -> list[sqlite3.Row]:
    """RRF 融合：FTS 行序与向量余弦序互为补充，只在一侧命中的文件仍保留。"""
    if not vec_hits:
        return rows[:limit]
    fts_rank = {int(r["id"]): rank for rank, r in enumerate(rows)}
    vec_rank = {fid: rank for rank, (fid, _score) in enumerate(vec_hits)}
    needed = [fid for fid, _s in vec_hits if fid not in fts_rank]
    extra_rows: list[sqlite3.Row] = []
    if needed:
        marks = ",".join("?" for _ in needed)
        sql = (
            f"SELECT f.*, 0.0 AS rank, substr(f.content, 1, 240) AS snippet "
            f"FROM files f WHERE f.id IN ({marks})"
        )
        params: list[Any] = list(needed)
        if ext:
            sql += " AND f.ext = ?"
            params.append(ext.lower())
        if root_id is not None:
            sql += " AND f.root_id = ?"
            params.append(root_id)
        fetched = {int(r["id"]): r for r in conn.execute(sql, params).fetchall()}
        order = {fid: rank for rank, fid in enumerate(needed)}
        extra_rows = sorted(
            (fetched[fid] for fid in needed if fid in fetched),
            key=lambda r: order[int(r["id"])],
        )
    pool = list(rows) + [r for r in extra_rows if int(r["id"]) not in fts_rank]
    constant = 60.0

    def rrf_score(row: sqlite3.Row) -> float:
        fid = int(row["id"])
        score = 0.0
        if fid in fts_rank:
            score += 1.0 / (constant + fts_rank[fid])
        if fid in vec_rank:
            score += 1.0 / (constant + vec_rank[fid])
        return score

    pool.sort(key=rrf_score, reverse=True)
    return pool[:limit]


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int | None = None,
    ext: str = "",
    root: str = "",
) -> list[dict[str, Any]]:
    config = load_config()
    limit = limit or int(config["search"].get("default_limit", 20))
    root_id = None
    if root:
        row = root_for_path(conn, root)
        if row is None:
            raise ValueError(f"索引范围里没有这个路径：{root}")
        root_id = int(row["id"])

    if ext and not ext.startswith("."):
        ext = "." + ext

    rows: list[sqlite3.Row] = []
    if _looks_like_path(query):
        path_rows = search_path(conn, query, limit=limit, ext=ext, root_id=root_id)
        if path_rows:
            return [_row_to_result(row) for row in path_rows]
        match_query = make_fts_query(query)
        if not match_query:
            return []
        try:
            rows = search_rows(conn, match_query, limit=limit, ext=ext, root_id=root_id)
        except sqlite3.OperationalError:
            rows = []
        return [_row_to_result(row) for row in rows]

    match_query = make_fts_query(query)
    candidate_limit = max(limit * 3, limit)
    if match_query:
        try:
            rows = search_rows(conn, match_query, limit=candidate_limit, ext=ext, root_id=root_id)
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            terms = query_terms(query)
            rows = search_like_terms(conn, terms, limit=candidate_limit, ext=ext, root_id=root_id)
        if not rows:
            rows = search_like(conn, query, limit=candidate_limit, ext=ext, root_id=root_id)
    else:
        rows = search_like(conn, "", limit=candidate_limit, ext=ext, root_id=root_id)
    # 混合检索：FTS 候选与语义向量 RRF 融合（无向量/模型不可用时退化为纯 FTS）
    vec_hits = vector_search(conn, query, top=max(limit * 2, 40))
    rows = _hybrid_merge(conn, rows, vec_hits, limit=limit, ext=ext, root_id=root_id)
    return [_row_to_result(row) for row in rows]


def get_file(conn: sqlite3.Connection, path: str | Path, *, max_chars: int = 5000) -> dict[str, Any]:
    resolved = ensure_allowed_path(conn, path)
    result: dict[str, Any] = {
        "path": str(resolved),
        "exists": resolved.exists(),
        "is_dir": resolved.is_dir() if resolved.exists() else False,
        "indexed": False,
        "content": "",
        "content_truncated": False,
    }
    if not resolved.exists():
        return result

    if resolved.is_dir():
        entries = list_directory(conn, resolved, depth=1, limit=200)
        result["entries"] = entries
        return result

    stat = resolved.stat()
    result.update({
        "name": resolved.name,
        "ext": resolved.suffix.lower(),
        "size": stat.st_size,
        "size_text": _fmt_size(stat.st_size),
        "mtime": stat.st_mtime,
    })

    row = get_file_by_path(conn, resolved)
    content = ""
    status = "not_indexed"
    if row is not None:
        result["indexed"] = True
        result["content_status"] = row["content_status"]
        result["db_error"] = row["error"]
        content = row["content"] or ""
        status = row["content_status"]
    else:
        max_bytes = int(load_config()["index"].get("max_file_size_mb", 20)) * 1024 * 1024
        if stat.st_size <= max_bytes:
            text, status, error = extract.extract_text(resolved)
            content = extract.normalize_text(text)
            result["content_status"] = status
            if error:
                result["db_error"] = error
        else:
            status = "too_large"
            result["content_status"] = status

    if content:
        result["content"] = content[:max_chars]
        result["content_truncated"] = len(content) > max_chars
    result["status"] = status
    return result


def _dir_sort_key(item: Path) -> tuple[bool, str]:
    try:
        is_dir = item.is_dir()
    except OSError:
        is_dir = False
    return (not is_dir, item.name.lower())


def list_directory(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    depth: int = 1,
    limit: int = 500,
) -> list[dict[str, Any]]:
    resolved = ensure_allowed_path(conn, path)
    if not resolved.exists() or not resolved.is_dir():
        raise NotADirectoryError(f"不是目录：{resolved}")

    entries: list[dict[str, Any]] = []
    queue: list[tuple[Path, int]] = [(resolved, 0)]
    while queue and len(entries) < limit:
        current, level = queue.pop(0)
        try:
            children = sorted(current.iterdir(), key=_dir_sort_key)
        except OSError:
            continue
        for child in children:
            if len(entries) >= limit:
                break
            try:
                stat = child.stat()
                is_dir = child.is_dir()
            except OSError:
                continue
            row = get_file_by_path(conn, child)
            entries.append({
                "path": str(child),
                "name": child.name,
                "is_dir": is_dir,
                "size": stat.st_size,
                "size_text": _fmt_size(stat.st_size),
                "mtime": stat.st_mtime,
                "indexed": row is not None,
                "content_status": row["content_status"] if row is not None else "",
            })
            if is_dir and level + 1 < depth:
                queue.append((child, level + 1))
    return entries


def summarize_folder(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    max_files: int = 2000,
    max_samples: int = 8,
) -> dict[str, Any]:
    resolved = ensure_allowed_path(conn, path)
    if not resolved.exists() or not resolved.is_dir():
        raise NotADirectoryError(f"不是目录：{resolved}")

    config = load_config()
    exclude_dirs = {str(item).lower() for item in config["index"].get("exclude_dirs", [])}
    exclude_globs = list(config["index"].get("exclude_globs", []))

    file_rows: list[dict[str, Any]] = []
    dir_count = 0
    total_bytes = 0
    ext_counts: dict[str, int] = {}

    for current, dirnames, filenames in os.walk(
        resolved, followlinks=False, onerror=lambda _error: None
    ):
        dirnames[:] = [name for name in dirnames if name.lower() not in exclude_dirs]
        dir_count += len(dirnames)
        for filename in filenames:
            candidate = Path(current) / filename
            if extract.is_excluded(candidate, exclude_globs) or extract.is_sensitive(candidate):
                continue
            try:
                stat = candidate.stat()
            except OSError:
                continue
            ext = candidate.suffix.lower() or "(无扩展名)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            total_bytes += stat.st_size
            file_rows.append({
                "path": str(candidate),
                "name": candidate.name,
                "ext": ext,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
            if len(file_rows) >= max_files:
                break
        if len(file_rows) >= max_files:
            break

    file_rows.sort(key=lambda item: item["mtime"], reverse=True)
    samples = []
    for item in file_rows:
        if len(samples) >= max_samples:
            break
        candidate = Path(item["path"])
        if candidate.suffix.lower() not in extract.TEXT_EXTS:
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            samples.append({"name": item["name"], "text": text[:300]})

    top_exts = sorted(ext_counts.items(), key=lambda pair: pair[1], reverse=True)[:8]
    summary_lines = [
        f"目录：{resolved}",
        f"文件：{len(file_rows)} 个（最多统计 {max_files} 个），子目录：{dir_count} 个，总大小：{_fmt_size(total_bytes)}",
        "主要类型：" + "、".join(f"{ext} {count} 个" for ext, count in top_exts),
    ]
    if file_rows:
        summary_lines.append("最近修改：")
        for item in file_rows[:5]:
            summary_lines.append(f"  - {item['name']}（{_fmt_size(item['size'])}）")
    if samples:
        summary_lines.append("内容片段：")
        for item in samples:
            summary_lines.append(f"  - {item['name']}：{item['text'][:120]}")

    return {
        "path": str(resolved),
        "files": len(file_rows),
        "dirs": dir_count,
        "bytes": total_bytes,
        "bytes_text": _fmt_size(total_bytes),
        "exts": dict(top_exts),
        "recent": file_rows[:10],
        "samples": samples,
        "summary": "\n".join(summary_lines),
    }


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def _terms(question: str) -> list[str]:
    words = [word for word in re.split(r"\s+", question.strip()) if word]
    terms: list[str] = []
    for word in words:
        terms.append(word)
        if len(word) > 2:
            terms.extend(word[index:index + 2] for index in range(len(word) - 1))
    return list(dict.fromkeys(term for term in terms if term))


def _sources(hits: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return [
        {
            "name": hit["name"],
            "path": hit["path"],
            "snippet": hit.get("snippet", ""),
            "content_status": hit.get("content_status", ""),
        }
        for hit in hits[:limit]
    ]


def _ollama_context(conn: sqlite3.Connection, hits: list[dict[str, Any]]) -> str:
    config = load_config()
    local = config.get("models", {}).get("local", {})
    max_chars = int(local.get("context_chars", 6000))
    max_sources = int(local.get("max_sources", 5))
    parts: list[str] = []
    for hit in hits[:max_sources]:
        row = get_file_by_path(conn, hit["path"])
        chunks: list[sqlite3.Row] = []
        if row is not None:
            chunks = conn.execute(
                "SELECT text FROM chunks WHERE file_id = ? ORDER BY chunk_index LIMIT 2",
                (int(row["id"]),),
            ).fetchall()
        for chunk in chunks:
            text = re.sub(r"\s+", " ", chunk["text"]).strip()
            if text:
                parts.append(f"[{len(parts) + 1}] {hit['name']}：{text[:900]}")
        if not chunks and hit.get("snippet"):
            parts.append(f"[{len(parts) + 1}] {hit['name']}：{hit['snippet'][:500]}")
    return "\n\n".join(parts)[:max_chars]


def _ollama_answer(
    conn: sqlite3.Connection,
    question: str,
    hits: list[dict[str, Any]],
    model: str,
) -> tuple[str | None, str]:
    context = _ollama_context(conn, hits)
    name, persona = _assistant_profile(load_config())
    messages = [
        {
            "role": "system",
            "content": (
                f"你是{name}，{persona}。"
                "用中文像朋友聊天一样回答，简短自然，一般不超过三句话。"
                "只根据资料回答，不要编造；资料不足就直说，不要硬凑依据。"
                "不要输出完整文件路径，只提文件名或目录名；不要自称 Kimi Code、WebChat 或其它产品。"
                "除非用户明确要求，不要逐条列来源、不要写“结论/依据”这种格式。"
            ),
        },
        {
            "role": "user",
            "content": f"问题：{question}\n\n资料：\n{context or '（没有检索到本地资料）'}",
        },
    ]
    try:
        answer = llm.chat(model, messages)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    if not answer:
        return None, "模型没有返回内容"
    return answer, ""


def ask(conn: sqlite3.Connection, question: str, *, limit: int = 5) -> dict[str, Any]:
    config = load_config()
    mode = str(config.get("models", {}).get("mode", "none")).lower()
    model = str(config.get("models", {}).get("local", {}).get("chat", llm.DEFAULT_MODEL)).strip()
    name, persona = _assistant_profile(config)

    if _IDENTITY_PATTERN.match(question):
        return {
            "question": question,
            "answer": (
                f"我是{name}，{persona}。"
                "可以帮你找文件、总结资料、整理目录，也能回答知识库里的问题。"
            ),
            "sources": [],
            "mode": "persona",
            "model": "",
        }

    hits = search(conn, question, limit=max(limit * 2, limit))
    sources = _sources(hits, limit)
    local = config.get("models", {}).get("local", {})
    fallback_note = ""
    if mode == "ollama" and (hits or bool(local.get("answer_without_context", False))):
        cache_key = f"{model}\x00{name}\x00{persona}\x00{question}"
        cache_seconds = int(local.get("cache_seconds", 600))
        cached = _OLLAMA_CACHE.get(cache_key)
        answer = ""
        error = ""
        if cached and time.time() - cached[0] < cache_seconds:
            answer = cached[1]
        else:
            answer, error = _ollama_answer(conn, question, hits, model)
            if answer:
                if len(_OLLAMA_CACHE) > 100:
                    _OLLAMA_CACHE.clear()
                _OLLAMA_CACHE[cache_key] = (time.time(), answer)
        if answer:
            return {
                "question": question,
                "answer": answer[:4000],
                "sources": sources,
                "mode": "ollama",
                "model": model,
                "cached": bool(cached and time.time() - cached[0] < cache_seconds),
            }
        fallback_note = f"\n\n（本地模型不可用，已回退到摘录式：{error}）"

    if not hits:
        return {
            "question": question,
            "answer": (
                f"本地资料中没有找到与「{question}」相关的内容。"
                "可以换个说法，或先运行 `kb index` 更新索引。"
                + fallback_note
            ),
            "sources": [],
            "mode": "extractive",
        }

    terms = _terms(question)
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for hit in hits:
        row = get_file_by_path(conn, hit["path"])
        if row is None:
            continue
        chunks = conn.execute(
            "SELECT text FROM chunks WHERE file_id = ? ORDER BY chunk_index",
            (int(row["id"]),),
        ).fetchall()
        for chunk in chunks:
            for sentence in _sentences(chunk["text"]):
                score = sum(sentence.count(term) for term in terms)
                if score > 0:
                    scored.append((score, sentence, hit))
    scored.sort(key=lambda item: item[0], reverse=True)

    selected = []
    seen = set()
    for _, sentence, hit in scored:
        key = sentence[:60]
        if key in seen:
            continue
        seen.add(key)
        selected.append((sentence, hit))
        if len(selected) >= 5:
            break

    if not selected:
        for hit in hits[:3]:
            snippet = hit.get("snippet") or ""
            if snippet:
                selected.append((snippet, hit))

    lines = []
    for index, (sentence, hit) in enumerate(selected, 1):
        lines.append(f"{index}. {sentence}（来源：{hit['name']}）")

    answer = "根据本地资料，最相关的内容如下：\n" + "\n".join(lines)
    if not selected:
        answer = "本地资料里有相关文件，但没有提取到足够明确的句子。可以打开来源文件核对。"
    return {
        "question": question,
        "answer": (answer + fallback_note)[:4000],
        "sources": sources,
        "mode": "extractive",
    }


def format_search_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "没有找到匹配的文件。"
    lines = []
    for index, item in enumerate(results, 1):
        lines.append(
            f"{index}. {item['name']}  [{item['size_text']}]  {item['content_status']}\n"
            f"   {item['path']}"
        )
        if item.get("snippet"):
            lines.append(f"   {item['snippet'][:180]}")
    return "\n".join(lines)


def escape(value: str) -> str:
    return html.escape(value, quote=True)
