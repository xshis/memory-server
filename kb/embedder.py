from __future__ import annotations

import sqlite3
from typing import Any, Callable

import numpy as np

from . import llm
from .db import upsert_file_embedding

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
    model = llm.embed_model()
    status = llm.status()
    if not status.get("running"):
        raise RuntimeError("Ollama 未运行，先 `ollama serve` 或运行 `kb model`")
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
            vectors = llm.embed(texts, model)
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
