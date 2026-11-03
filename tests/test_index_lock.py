from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from kb.index_lock import IndexLock


class IndexLockTest(unittest.TestCase):
    def test_only_one_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.lock"
            first = IndexLock(path)
            second = IndexLock(path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            self.assertEqual(second.holder_pid(), str(os.getpid()))
            first.release()
            self.assertTrue(second.acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()
