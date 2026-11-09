from __future__ import annotations

import os
import sqlite3
from typing import Any, Callable

import numpy as np

from . import llm
from .db import upsert_file_embedding

try:
    from fastembed import TextEmbedding

    _FASTEMBED_AVAILABLE = True
except ImportError:
    TextEmbedding = None  # type: ignore[assignment,misc]
    _FASTEMBED_AVAILABLE = False

EMBED_TEXT_CHARS = 1200
BATCH_SIZE = 48
DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-zh-v1.5"

_TEXT_EMBEDDING = None


def fastembed_available() -> bool:
    return _FASTEMBED_AVAILABLE


def _fastembed_cache_dir() -> str:
    """模型缓存目录：配置 embed_cache_dir 优先，默认 ~/.cache/fastembed。

    fastembed 0.9.x 默认用系统临时目录（/var/folders/.../T），会被 macOS 定期清理
    导致重复下载；固定到家目录下可复用，也方便分享包预置模型离线安装。
    """
    from .config import load_config

    configured = str(
        load_config().get("models", {}).get("local", {}).get("embed_cache_dir", "")
    ).strip()
    if configured:
        return os.path.expanduser(configured)
    return os.path.join(os.path.expanduser("~"), ".cache", "fastembed")


def _fastembed_model():
    global _TEXT_EMBEDDING
    if not _FASTEMBED_AVAILABLE:
        return None
    if _TEXT_EMBEDDING is None:
        from .config import load_config

        configured = str(
            load_config().get("models", {}).get("local", {}).get("embed", "")
        ).strip()
        candidates = [configured, DEFAULT_FASTEMBED_MODEL] if configured else [DEFAULT_FASTEMBED_MODEL]
        cache_dir = _fastembed_cache_dir()
        for candidate in candidates:
            if not candidate or "/" not in candidate:
                continue  # ollama 风格的名字（如 nomic-embed-text）不是 fastembed 模型
            try:
                _TEXT_EMBEDDING = TextEmbedding(model_name=candidate, cache_dir=cache_dir)
                return _TEXT_EMBEDDING
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError("fastembed 模型加载失败（可配置 models.local.embed 为可用模型）")
    return _TEXT_EMBEDDING


def embed_texts(texts: list[str]) -> list[list[float]]:
    """嵌入文本：优先 fastembed（ONNX 本地、不依赖 Ollama），缺装时回退 Ollama。"""
    texts = [str(text) for text in texts]
    if not texts:
        return []
    model = _fastembed_model()
    if model is not None:
        return [[float(value) for value in vector] for vector in model.embed(texts)]
    return llm.embed(texts, llm.embed_model())

EMBED_TEXT_CHARS = 1200
BATCH_SIZE = 48

Progress = Callable[[str], None]


def _default_progress(message: str) -> None:
    print(message, flush=True)


def embed_text_for_file(name: str, content: str) -> str:
    """拼嵌入文本：文件名 + 正文前 1200 字符（nomic 上下文 8192，够用且省算力）。"""
    body = str(content or "")[:EMBED_TEXT_CHARS].strip()
    return f"{str(name or '').strip()}\n{body}".strip()


def quantize_vector(vector: list[float]) -> tuple[bytes, float, int]:
    """单位化后量化成 int8。返回 (blob, 原范数, 维度)。cosine 相似度 = 两 int8 向量点积 / (127*127)。"""
    arr = np.asarray(vector, dtype=np.float32)
    dim = int(arr.size)
    if dim == 0:
        raise ValueError("空向量")
    norm = float(np.linalg.norm(arr))
    if norm <= 0:
        arr = np.full(dim, 1.0 / np.sqrt(dim), dtype=np.float32)
        norm = 1.0
    unit = arr / norm
    quantized = np.clip(np.round(unit * 127.0), -127, 127).astype(np.int8)
    return quantized.tobytes(), norm, dim


def embed_missing(
    conn: sqlite3.Connection,
    *,
    progress: Progress | None = None,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
) -> dict[str, Any]:
    """给还没有向量的文件补嵌入。可反复执行（天然断点续跑）。"""
    progress = progress or _default_progress
    if _FASTEMBED_AVAILABLE:
        try:
            _fastembed_model()
        except RuntimeError as exc:
            raise RuntimeError(f"嵌入模型加载失败：{exc}") from exc
        model = DEFAULT_FASTEMBED_MODEL
    else:
        model = llm.embed_model()
        status = llm.status()
        if not status.get("running"):
            raise RuntimeError("嵌入不可用：安装 fastembed（pip install fastembed）或启动 Ollama")
        if not llm.model_available(model, status):
            raise RuntimeError(f"嵌入模型 {model} 未安装，先运行：ollama pull {model}")

    rows = conn.execute(
        """
        SELECT f.id, f.name, f.content
        FROM files f
        WHERE f.content_chars > 0
          AND NOT EXISTS (SELECT 1 FROM file_embeddings e WHERE e.file_id = f.id)
        ORDER BY f.id
        """
    ).fetchall()
    if limit is not None and limit > 0:
        rows = rows[: int(limit)]
    total = len(rows)
    if total == 0:
        return {"embedded": 0, "failed": 0, "total": 0, "model": model, "seconds": 0.0}

    import time

    started = time.time()
    embedded = 0
    failed = 0
    for offset in range(0, total, batch_size):
        batch = rows[offset : offset + batch_size]
        texts = [embed_text_for_file(row["name"], row["content"]) for row in batch]
        try:
            vectors = embed_texts(texts)
        except Exception as exc:  # noqa: BLE001
            failed += len(batch)
            progress(f"[失败] 第 {offset + 1}-{offset + len(batch)} 个：{exc}")
            continue
        if len(vectors) != len(batch):
            failed += len(batch)
            progress(f"[失败] 第 {offset + 1}-{offset + len(batch)} 个：返回数量不符")
            continue
        for row, vector in zip(batch, vectors):
            try:
                data, norm, dim = quantize_vector(vector)
                upsert_file_embedding(
                    conn,
                    file_id=int(row["id"]),
                    model=model,
                    dim=dim,
                    norm=norm,
                    data=data,
                )
                embedded += 1
            except Exception:  # noqa: BLE001
                failed += 1
        conn.commit()
        done = embedded + failed
        percent = done * 100 // total
        elapsed = time.time() - started
        speed = done / elapsed if elapsed > 0 else 0.0
        progress(
            f"[嵌入] {done}/{total} ({percent}%)，失败 {failed}，"
            f"{speed:.1f} 个/秒，模型 {model}"
        )
    return {
        "embedded": embedded,
        "failed": failed,
        "total": total,
        "model": model,
        "seconds": round(time.time() - started, 2),
    }
