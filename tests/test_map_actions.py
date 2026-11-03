from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kb import map_server


class TrashAndRevealTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "home"
        self.root.mkdir()
        self.trash = Path(self.tmp.name) / "trash"
        self.db = Path(self.tmp.name) / "kb.db"
        (self.root / "dup1.txt").write_text("same", encoding="utf-8")
        (self.root / "dup2.txt").write_text("same", encoding="utf-8")
        (self.root / "empty.txt").write_text("", encoding="utf-8")
        (self.root / "normal.txt").write_text("different", encoding="utf-8")
        conn = sqlite3.connect(self.db)
        conn.executescript(
            """
            CREATE TABLE roots (id INTEGER PRIMARY KEY, path TEXT, label TEXT);
            CREATE TABLE files_fts (rowid INTEGER PRIMARY KEY, content TEXT);
            CREATE TABLE chunks (id INTEGER PRIMARY KEY, file_id INTEGER);
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                root_id INTEGER,
                path TEXT,
                rel_path TEXT,
                name TEXT,
                ext TEXT,
                size INTEGER,
                mtime REAL,
                content_status TEXT,
                sha256 TEXT
            );
            """
        )
        conn.execute("INSERT INTO roots VALUES (1, ?, 'home')", (str(self.root),))
        rows = [
            ("dup1.txt", 4, "same"),
            ("dup2.txt", 4, "same"),
            ("empty.txt", 0, ""),
            ("normal.txt", 9, "other"),
        ]
        for index, (name, size, digest) in enumerate(rows, start=1):
            conn.execute(
                "INSERT INTO files(id, root_id, path, rel_path, name, ext, size, mtime, content_status, sha256) "
                "VALUES (?,1,?,?,?,?,?,?,?,?)",
                (index, str(self.root / name), name, name, ".txt", size, 1.0, "indexed", digest),
            )
        conn.commit()
        conn.close()
        self.state = map_server.GraphState(
            Path(self.tmp.name), self.db, max_files=10, interval=60,
        )
        self.state.request_rebuild = lambda: None  # type: ignore[method-assign]
        self.patch = mock.patch.object(map_server, "trash_root", return_value=self.trash)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.tmp.cleanup()

    def test_requires_confirmation(self) -> None:
        result = self.state.trash(["dup2.txt"], confirm=False)
        self.assertIn("error", result)
        self.assertTrue((self.root / "dup2.txt").exists())

    def test_moves_duplicate_to_trash_and_updates_db(self) -> None:
        result = self.state.trash(["dup2.txt"], confirm=True)
        self.assertEqual(result["moved_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertFalse((self.root / "dup2.txt").exists())
        self.assertTrue((self.trash / "dup2.txt").exists())
        conn = sqlite3.connect(self.db)
        count = conn.execute("SELECT COUNT(*) FROM files WHERE rel_path='dup2.txt'").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_refuses_normal_file(self) -> None:
        result = self.state.trash(["normal.txt"], confirm=True)
        self.assertEqual(result["moved_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertTrue((self.root / "normal.txt").exists())

    def test_trash_reason(self) -> None:
        duplicates = self.state._duplicate_counts()
        self.assertEqual(self.state._trash_reason({"name": "empty.txt", "size": 0}, duplicates), "空文件")
        self.assertEqual(self.state._trash_reason({"name": "a.tmp", "size": 3}, duplicates), "临时文件")
        self.assertEqual(
            self.state._trash_reason({"name": "dup1.txt", "size": 4, "sha256": "same"}, duplicates),
            "重复文件",
        )
        self.assertEqual(
            self.state._trash_reason({"name": "normal.txt", "size": 9, "sha256": "other"}, duplicates),
            "",
        )

    def test_reveal_uses_reveal_command(self) -> None:
        with mock.patch.object(map_server.subprocess, "Popen") as popen:
            result = self.state.reveal("dup1.txt")
        self.assertTrue(result.get("ok"))
        command = popen.call_args[0][0]
        self.assertEqual(command[0], "open")
        self.assertIn("-R", command)


if __name__ == "__main__":
    unittest.main()
