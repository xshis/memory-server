from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kb import db, embedder, search


def _fixture_conn(tmp: str) -> sqlite3.Connection:
    conn = db.connect(Path(tmp) / "kb.db")
    db.init_schema(conn)
    root_id = db.add_root(conn, Path(tmp) / "docs")
    (Path(tmp) / "docs").mkdir(exist_ok=True)
    return conn


def _put_file(conn: sqlite3.Connection, tmp: str, name: str, content: str) -> int:
    path = Path(tmp) / "docs" / name
    path.write_text(content, encoding="utf-8")
    return db.upsert_file(
        conn,
        root_id=1,
        path=path,
        rel_path=name,
        name=name,
        ext=Path(name).suffix.lower(),
        size=path.stat().st_size,
        mtime=path.stat().st_mtime,
        ctime=path.stat().st_ctime,
        content=content,
        content_status="indexed",
        error="",
        sha256="",
        chunks=[content],
        last_seen_run="r1",
    )


class QuantizeTest(unittest.TestCase):
    def test_cosine_preserved(self) -> None:
        a = [1.0, 0.0, 0.0, 0.0]
        b = [0.9, 0.1, 0.0, 0.0]
        qa, _, da = embedder.quantize_vector(a)
        qb, _, db_ = embedder.quantize_vector(b)
        self.assertEqual(da, db_)
        import numpy as np

        va = np.frombuffer(qa, dtype=np.int8).astype(float)
        vb = np.frombuffer(qb, dtype=np.int8).astype(float)
        cosine = float(va @ vb / ((va @ va) ** 0.5 * (vb @ vb) ** 0.5))
        self.assertGreater(cosine, 0.99)

    def test_zero_vector_safe(self) -> None:
        data, norm, dim = embedder.quantize_vector([0.0, 0.0, 0.0])
        self.assertEqual(dim, 3)
        self.assertGreater(norm, 0)


class EmbedMissingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = _fixture_conn(self.tmp.name)
        self.file_id = _put_file(self.conn, self.tmp.name, "a.txt", "hello world")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _patched(self, vectors: list[list[float]]):
        from contextlib import ExitStack

        stack = ExitStack()
        # 强制走 Ollama 分支并假装模型在线（真实环境优先 fastembed）
        stack.enter_context(mock.patch.object(embedder, "_FASTEMBED_AVAILABLE", False))
        stack.enter_context(mock.patch.object(
            embedder.llm, "status",
            return_value={"running": True, "models": ["nomic-embed-text"]},
        ))
        stack.enter_context(mock.patch.object(embedder.llm, "model_available", return_value=True))
        stack.enter_context(mock.patch.object(embedder, "embed_texts", return_value=vectors))
        stack.enter_context(mock.patch.object(embedder.llm, "embed", return_value=vectors))
        return stack

    def test_embeds_missing_and_resumes(self) -> None:
        with self._patched([[1.0, 0.0, 0.0]]):
            stats = embedder.embed_missing(self.conn)
        self.assertEqual(stats["embedded"], 1)
        self.assertEqual(stats["failed"], 0)
        stats = db.embedding_stats(self.conn)
        self.assertEqual(stats["embedded"], 1)
        # 再跑一次：断点续跑，没有新工作
        with self._patched([[1.0, 0.0, 0.0]]):
            stats = embedder.embed_missing(self.conn)
        self.assertEqual(stats["embedded"], 0)

    def test_reupsert_clears_stale_vector(self) -> None:
        with self._patched([[1.0, 0.0, 0.0]]):
            embedder.embed_missing(self.conn)
        self.assertEqual(db.embedding_stats(self.conn)["embedded"], 1)
        _put_file(self.conn, self.tmp.name, "a.txt", "changed content")
        self.assertEqual(db.embedding_stats(self.conn)["embedded"], 0)

    def test_delete_file_removes_vector(self) -> None:
        with self._patched([[1.0, 0.0, 0.0]]):
            embedder.embed_missing(self.conn)
        db.delete_file_by_id(self.conn, self.file_id)
        self.assertEqual(db.embedding_stats(self.conn)["embedded"], 0)


class HybridSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = _fixture_conn(self.tmp.name)
        # 文件名都不含关键词，只能靠语义向量召回
        self.f_cat = _put_file(self.conn, self.tmp.name, "memo.txt", "feline pet care notes")
        self.f_car = _put_file(self.conn, self.tmp.name, "notes.txt", "turbo engine piston torque specs")
        cat_vec = [1.0, 0.0, 0.0, 0.0]
        car_vec = [0.0, 1.0, 0.0, 0.0]
        for fid, vec in ((self.f_cat, cat_vec), (self.f_car, car_vec)):
            data, norm, dim = embedder.quantize_vector(vec)
            db.upsert_file_embedding(
                self.conn, file_id=fid, model="nomic-embed-text",
                dim=dim, norm=norm, data=data,
            )

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _embed_impl(self, texts: list[str], *args) -> list[list[float]]:
        # 问猫相关 → 猫向量；否则 → 车向量
        out = []
        for text in texts:
            out.append([1.0, 0.0, 0.0, 0.0] if "猫" in text or "cat" in text.lower() else [0.0, 1.0, 0.0, 0.0])
        return out

    def test_vector_search_ranks_by_cosine(self) -> None:
        with mock.patch.object(embedder, "embed_texts", side_effect=self._embed_impl):
            hits = search.vector_search(self.conn, "我的猫怎么养", top=5)
        self.assertEqual(hits[0][0], self.f_cat)
        self.assertGreater(hits[0][1], 0.99)

    def test_semantic_recall_when_fts_misses(self) -> None:
        with mock.patch.object(embedder, "embed_texts", side_effect=self._embed_impl):
            results = search.search(self.conn, "猫", limit=5)
        names = [r["name"] for r in results]
        self.assertIn("memo.txt", names)
        self.assertEqual(names[0], "memo.txt")

    def test_pure_fts_when_no_embeddings(self) -> None:
        self.conn.execute("DELETE FROM file_embeddings")
        self.conn.commit()
        results = search.search(self.conn, "feline", limit=5)
        self.assertEqual([r["name"] for r in results], ["memo.txt"])

    def test_hybrid_merge_rrf_prefers_both_sides(self) -> None:
        rows = self.conn.execute(
            "SELECT f.*, 0.0 AS rank, '' AS snippet FROM files f WHERE f.id = ?",
            (self.f_car,),
        ).fetchall()
        merged = search._hybrid_merge(
            self.conn, rows, [(self.f_cat, 0.9), (self.f_car, 0.1)],
            limit=5, ext="", root_id=None,
        )
        # f_car 双侧都命中（FTS 第1 + 向量第2），RRF 得分应高于单侧命中的 f_cat（向量第1）
        self.assertEqual(int(merged[0]["id"]), self.f_car)
        self.assertEqual(int(merged[1]["id"]), self.f_cat)


if __name__ == "__main__":
    unittest.main()
