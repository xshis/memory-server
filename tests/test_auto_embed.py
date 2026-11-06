"""auto_embed 的触发判断：缺口统计、运行中检测、拉起条件。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kb import auto_embed, db
from kb.index_lock import IndexLock


def _make_kb_home(tmp: str, files: list[tuple[str, str]]) -> Path:
    home = Path(tmp) / "kbhome"
    (home / "data").mkdir(parents=True)
    conn = db.connect(home / "data" / "kb.db")
    db.init_schema(conn)
    db.add_root(conn, Path(tmp) / "docs")
    docs = Path(tmp) / "docs"
    docs.mkdir(exist_ok=True)
    for name, content in files:
        path = docs / name
        path.write_text(content, encoding="utf-8")
        db.upsert_file(
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
    conn.commit()
    conn.close()
    return home


class EmbeddingBriefTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = _make_kb_home(self.tmp.name, [("a.txt", "hello"), ("b.txt", "world")])
        self.db_path = self.home / "data" / "kb.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_gap_counts_unembedded_files(self) -> None:
        brief = auto_embed.embedding_brief(self.db_path)
        self.assertEqual(brief["embeddable"], 2)
        self.assertEqual(brief["embedded"], 0)
        self.assertEqual(brief["gap"], 2)
        self.assertFalse(brief["running"])

    def test_gap_shrinks_after_embedding(self) -> None:
        from kb.embedder import quantize_vector

        blob, norm, dim = quantize_vector([1.0, 0.0, 0.0])
        conn = db.connect(self.db_path)
        db.upsert_file_embedding(conn, file_id=1, model="m", dim=dim, norm=norm, data=blob)
        conn.commit()
        conn.close()
        brief = auto_embed.embedding_brief(self.db_path)
        self.assertEqual(brief["embedded"], 1)
        self.assertEqual(brief["gap"], 1)

    def test_missing_db_returns_empty(self) -> None:
        brief = auto_embed.embedding_brief(self.home / "data" / "none.db")
        self.assertEqual(brief["embeddable"], 0)
        self.assertEqual(brief["gap"], 0)


class EmbedRunningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = _make_kb_home(self.tmp.name, [("a.txt", "hello")])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_lock_reflects_running_state(self) -> None:
        lock = IndexLock(self.home / "data" / auto_embed.EMBED_LOCK_NAME)
        self.assertFalse(auto_embed.embed_running(self.home))
        self.assertTrue(lock.acquire())
        try:
            self.assertTrue(auto_embed.embed_running(self.home))
            brief = auto_embed.embedding_brief(self.home / "data" / "kb.db")
            self.assertTrue(brief["running"])
        finally:
            lock.release()
        self.assertFalse(auto_embed.embed_running(self.home))


@mock.patch.dict(os.environ, {"KB_AUTO_EMBED": "1"})
class MaybeAutoEmbedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = _make_kb_home(self.tmp.name, [("a.txt", "hello")])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _patch_backend(self, available: bool = True):
        return mock.patch.object(auto_embed, "_backend_available", return_value=available)

    def test_spawns_embed_when_gap_over_threshold(self) -> None:
        with self._patch_backend(), \
                mock.patch.object(auto_embed.subprocess, "Popen") as popen:
            kicked = auto_embed.maybe_auto_embed(self.home, reason="test")
        self.assertTrue(kicked)
        popen.assert_called_once()
        args = popen.call_args.args[0]
        self.assertEqual(args[-3:], ["-m", "kb", "embed"])
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.home))

    def test_skips_when_no_gap(self) -> None:
        empty_home = _make_kb_home(self.tmp.name + "-empty", [])
        with self._patch_backend(), \
                mock.patch.object(auto_embed.subprocess, "Popen") as popen:
            kicked = auto_embed.maybe_auto_embed(empty_home, reason="test")
        self.assertFalse(kicked)
        popen.assert_not_called()

    def test_skips_when_backend_unavailable(self) -> None:
        with self._patch_backend(available=False), \
                mock.patch.object(auto_embed.subprocess, "Popen") as popen:
            kicked = auto_embed.maybe_auto_embed(self.home, reason="test")
        self.assertFalse(kicked)
        popen.assert_not_called()

    def test_skips_when_embed_already_running(self) -> None:
        lock = IndexLock(self.home / "data" / auto_embed.EMBED_LOCK_NAME)
        self.assertTrue(lock.acquire())
        try:
            with self._patch_backend(), \
                    mock.patch.object(auto_embed.subprocess, "Popen") as popen:
                kicked = auto_embed.maybe_auto_embed(self.home, reason="test")
            self.assertFalse(kicked)
            popen.assert_not_called()
        finally:
            lock.release()

    def test_skips_when_db_missing(self) -> None:
        bare = Path(self.tmp.name) / "bare"
        bare.mkdir()
        with self._patch_backend(), \
                mock.patch.object(auto_embed.subprocess, "Popen") as popen:
            kicked = auto_embed.maybe_auto_embed(bare, reason="test")
        self.assertFalse(kicked)
        popen.assert_not_called()

    def test_env_switch_disables_auto_embed(self) -> None:
        with mock.patch.dict(os.environ, {"KB_AUTO_EMBED": "0"}), \
                self._patch_backend(), \
                mock.patch.object(auto_embed.subprocess, "Popen") as popen:
            kicked = auto_embed.maybe_auto_embed(self.home, reason="test")
        self.assertFalse(kicked)
        popen.assert_not_called()

    def test_startup_threshold_requires_big_gap(self) -> None:
        with self._patch_backend(), \
                mock.patch.object(auto_embed.subprocess, "Popen"):
            kicked = auto_embed.maybe_auto_embed(
                self.home, reason="map-start",
                gap_threshold=auto_embed.MIN_START_GAP,
            )
        self.assertFalse(kicked)


if __name__ == "__main__":
    unittest.main()
