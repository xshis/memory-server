from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from kb.db import delete_under_path
from kb.watch_events import _ignore


class WatchEventsTest(unittest.TestCase):
    def test_delete_under_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            db = root / "kb.db"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE roots (id INTEGER PRIMARY KEY, path TEXT, label TEXT);
                CREATE TABLE files_fts (rowid INTEGER PRIMARY KEY, content TEXT);
                CREATE TABLE chunks (id INTEGER PRIMARY KEY, file_id INTEGER);
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY,
                    root_id INTEGER,
                    path TEXT UNIQUE,
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
            target = root / "docs"
            (target / "sub").mkdir(parents=True)
            rows = [
                (1, str(target / "a.md"), "docs/a.md"),
                (2, str(target / "sub" / "b.md"), "docs/sub/b.md"),
                (3, str(root / "other.md"), "other.md"),
            ]
            for file_id, path, rel in rows:
                conn.execute(
                    "INSERT INTO files VALUES (?,1,?,?,?,?,?,?,?,?)",
                    (file_id, path, rel, Path(path).name, Path(path).suffix, 1, 1.0, "indexed", ""),
                )
            conn.commit()
            removed = delete_under_path(conn, target)
            left = [row[0] for row in conn.execute("SELECT rel_path FROM files ORDER BY id")]
            conn.close()
            self.assertEqual(removed, 2)
            self.assertEqual(left, ["other.md"])

    def test_ignore_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"index": {"exclude_dirs": ["Library"], "exclude_globs": ["*.tmp"]}}
            blocked = [root / "data"]
            self.assertTrue(_ignore(root / "data" / "kb.db", config, blocked))
            self.assertTrue(_ignore(root / "Library" / "x.md", config, blocked))
            self.assertTrue(_ignore(root / "docs" / ".env", config, blocked))
            self.assertTrue(_ignore(root / "docs" / "a.tmp", config, blocked))
            self.assertFalse(_ignore(root / "docs" / "a.md", config, blocked))


if __name__ == "__main__":
    unittest.main()
