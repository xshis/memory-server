from __future__ import annotations

import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from kb.map_server import build_graph


class GraphTest(unittest.TestCase):
    def make_db(self, root: Path) -> Path:
        path = root / "kb.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE roots (id INTEGER PRIMARY KEY, path TEXT, label TEXT);
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                root_id INTEGER,
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
        conn.execute("INSERT INTO roots VALUES (1, '/tmp/home', 'home')")
        rows = [
            (1, "Documents/personal-kb/a.md", "a.md", ".md", 1000, 100.0, "indexed", "same"),
            (1, "Documents/personal-kb/b.md", "b.md", ".md", 1000, 101.0, "indexed", "same"),
            (1, "Pictures/photo.jpg", "photo.jpg", ".jpg", 3000, 102.0, "unsupported", "photo"),
            (1, ".cursor/cache.js", "cache.js", ".js", 4000, 103.0, "indexed", "cache"),
            (1, "Downloads/empty.txt", "empty.txt", ".txt", 0, 104.0, "indexed", ""),
        ]
        conn.executemany(
            "INSERT INTO files(root_id, rel_path, name, ext, size, mtime, content_status, sha256) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()
        return path

    def test_tags_recommendations_and_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self.make_db(Path(tmp))
            graph: Dict[str, Any] = build_graph(db, max_files=10)
            summary = graph["summary"]
            self.assertEqual(summary["files"], 5)
            tags = {tag["id"] for tag in graph["tags"]}
            self.assertIn("cat:文档", tags)
            self.assertIn("dir:Documents", tags)
            self.assertIn("proj:personal-kb", tags)
            self.assertIn("rec:duplicate", tags)
            self.assertIn("rec:empty", tags)
            recommendations = {item["id"]: item for item in graph["recommendations"]}
            self.assertIn("organize_suggestions", graph)
            self.assertEqual(recommendations["duplicate"]["count"], 1)
            self.assertEqual(recommendations["empty"]["count"], 1)
            self.assertEqual(graph["nodes"][0]["id"], "root")
            files = [node for node in graph["nodes"] if node["kind"] == "file"]
            self.assertTrue(files)
            self.assertTrue(all("x" in node and "y" in node and "z" in node for node in files))
            self.assertTrue(all(len(node.get("glyph", "")) == 1 for node in files))
            self.assertTrue(all(node.get("word") for node in files))
            tag_nodes = [node for node in graph["nodes"] if node["kind"] == "tag"]
            self.assertTrue(tag_nodes)
            self.assertTrue(all(node.get("glyph") and node.get("word") for node in tag_nodes))
            self.assertTrue(all(node.get("parent") for node in tag_nodes))
            categories = [node for node in tag_nodes if node["tagKind"] == "category"]
            self.assertTrue(categories)
            # 融合球体布局：所有节点（含分类）散布在半径 128~640 的球体体积内
            self.assertTrue(
                all(
                    128 <= math.sqrt(node["x"] ** 2 + node["y"] ** 2 + node["z"] ** 2) <= 640
                    for node in categories
                )
            )
            by_id = {node["id"]: node for node in graph["nodes"]}
            for node in files:
                # 融合球体布局：文件不再围绕所属分类，而是散布在整个球体体积内
                radius = math.sqrt(node["x"] ** 2 + node["y"] ** 2 + node["z"] ** 2)
                self.assertGreaterEqual(radius, 128)
                self.assertLessEqual(radius, 640)
            self.assertEqual(graph["links"], [])

    def test_glyphs(self) -> None:
        self.assertEqual(self.state_glyphs("package.json"), ("P", "PAC"))
        self.assertEqual(self.state_glyphs("安装与使用.md"), ("安", "安装"))
        self.assertEqual(self.state_glyphs(".env.local"), ("E", "ENV"))
        self.assertEqual(self.state_glyphs("7zip.exe"), ("7", "7ZI"))

    @staticmethod
    def state_glyphs(name: str):
        from kb.map_server import glyphs_of
        return glyphs_of(name)

    def test_max_files_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self.make_db(Path(tmp))
            graph = build_graph(db, max_files=2)
            files = [node for node in graph["nodes"] if node["kind"] == "file"]
            self.assertLessEqual(len(files), 2)


if __name__ == "__main__":
    unittest.main()
