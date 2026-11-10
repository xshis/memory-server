from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kb.junk_scan import scan_junk, trash_junk


class JunkScanTest(unittest.TestCase):
    def make_tree(self, root: Path) -> None:
        now = time.time()

        def touch(rel: str, size: int = 0, age_days: float = 0.0, content: bytes = b"") -> Path:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if content:
                path.write_bytes(content)
            else:
                with open(path, "wb") as handle:
                    handle.truncate(size)
            stamp = now - age_days * 86400
            os.utime(path, (stamp, stamp))
            return path

        touch("sub/.DS_Store")
        touch("sub/a.tmp", age_days=2)
        touch("sub/active.part", size=5000)                      # 下载中：不推荐
        touch("sub/dead.crdownload", size=8000, age_days=3)
        touch("sub/empty.txt", age_days=10)
        touch("logs/big.log", size=11 * 1024 * 1024, age_days=40)
        touch("dup1/x.bin", content=b"hello-bytes")
        touch("dup2/x.bin", content=b"hello-bytes", age_days=5)
        touch("node_modules/pkg/old.bin", size=60 * 1024 * 1024, age_days=40)
        touch("normal/报告.md", size=100)                        # 正常文件：不命中

    def test_scan_classifies_by_universal_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            report = scan_junk([root])
            categories = report["categories"]
            self.assertIn("temp", categories)
            self.assertIn("empty", categories)
            self.assertIn("old_log", categories)
            self.assertIn("duplicate", categories)
            self.assertIn("stale_cache", categories)
            # temp：.DS_Store + a.tmp + dead.crdownload = 3，下载中的 active.part 排除
            temp_paths = {item["name"] for item in categories["temp"]["items"]}
            self.assertEqual(categories["temp"]["count"], 3)
            self.assertIn(".DS_Store", temp_paths)
            self.assertIn("a.tmp", temp_paths)
            self.assertIn("dead.crdownload", temp_paths)
            self.assertNotIn("active.part", temp_paths)
            # 重复文件只列出多余的一份
            self.assertEqual(categories["duplicate"]["count"], 1)
            # 缓存目录（node_modules）里的大文件只进 stale_cache，且不可删
            self.assertNotIn("stale_cache", report["trashable_categories"])
            self.assertEqual(categories["stale_cache"]["count"], 1)
            # 正常文件与近期空文件不命中
            self.assertNotIn("normal", categories)
            self.assertGreater(report["total"]["bytes"], 0)

    def test_trash_junk_only_whitelist_and_uses_trash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            report = scan_junk([root])
            trash_dir = root / "fake-trash"
            trash_dir.mkdir()
            calls: list[str] = []

            def fake_move(path: Path) -> str:
                calls.append(str(path))
                destination = trash_dir / path.name
                os.rename(path, destination)
                return str(destination)

            with mock.patch("kb.junk_scan.move_to_trash", side_effect=fake_move):
                result = trash_junk(report)
            self.assertTrue(result["ok"])
            moved_names = {Path(item["path"]).name for item in result["moved"]}
            # 白名单四类进废纸篓
            self.assertIn(".DS_Store", moved_names)
            self.assertIn("a.tmp", moved_names)
            self.assertIn("dead.crdownload", moved_names)
            self.assertIn("empty.txt", moved_names)
            self.assertIn("big.log", moved_names)
            self.assertIn("x.bin", moved_names)
            # 缓存与正常文件不动；下载中的 active.part 也没被误删
            self.assertTrue((root / "node_modules/pkg/old.bin").exists())
            self.assertTrue((root / "normal/报告.md").exists())
            self.assertTrue((root / "sub/active.part").exists())
            self.assertEqual(len(calls), result["moved_count"])


if __name__ == "__main__":
    unittest.main()
