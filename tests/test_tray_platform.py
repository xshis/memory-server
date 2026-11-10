from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from kb.tray import run_tray


class TrayPlatformTest(unittest.TestCase):
    def test_non_darwin_gets_hint_without_rumps(self) -> None:
        """Windows/Linux 上 kb tray 必须给出明确提示并干净退出，不能尝试导入 rumps。"""
        buffer = io.StringIO()
        with mock.patch("kb.tray.sys.platform", "win32"), redirect_stdout(buffer):
            code = run_tray()
        output = buffer.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("Windows", output)
        self.assertIn("start.bat", output)


if __name__ == "__main__":
    unittest.main()
