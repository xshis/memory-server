from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from kb.tray import run_tray


class TrayPlatformTest(unittest.TestCase):
    def test_missing_windows_tray_dependency_gets_hint(self) -> None:
        """Windows 上缺 pystray 时给出安装提示并干净退出，不启动任何 GUI。"""
        buffer = io.StringIO()
        with mock.patch("kb.tray.sys.platform", "win32"), redirect_stdout(buffer):
            code = run_tray()
        output = buffer.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("pystray", output)

    def test_linux_gets_platform_hint(self) -> None:
        buffer = io.StringIO()
        with mock.patch("kb.tray.sys.platform", "linux"), redirect_stdout(buffer):
            code = run_tray()
        self.assertEqual(code, 1)
        self.assertIn("只支持 macOS 和 Windows", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
