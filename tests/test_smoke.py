from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="kb-test-"))
        cls.home = cls.tmp / "home"
        cls.docs = cls.tmp / "docs"
        (cls.docs / "sub").mkdir(parents=True)

        (cls.docs / "报销流程.md").write_text(
            "# 报销流程\n\n员工报销需要提交发票，并在系统中填写报销单。"
            "财务审核通过后 5 个工作日到账。\n",
            encoding="utf-8",
        )
        (cls.docs / "供应商准入.txt").write_text(
            "供应商准入需要提供营业执照、合同和联系人信息。\n",
            encoding="utf-8",
        )
        (cls.docs / "sub" / "会议纪要.md").write_text(
            "会议纪要：讨论了项目排期和验收标准。\n",
            encoding="utf-8",
        )
        (cls.docs / "sub" / "安装与使用.md").write_text(
            "安装与使用说明：按步骤执行即可。\n",
            encoding="utf-8",
        )
        (cls.docs / "noise.md").write_text(
            "这里只提到安装，没有别的关键词。\n",
            encoding="utf-8",
        )
        (cls.docs / ".env").write_text("SECRET_TOKEN=should_not_index\n", encoding="utf-8")
        (cls.docs / "large.txt").write_text("A" * (2 * 1024 * 1024), encoding="utf-8")

        cls.env = {**os.environ, "KB_HOME": str(cls.home)}
        cls.run_cli("init", "--root", str(cls.docs))

        config_path = cls.home / "config" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["index"]["max_file_size_mb"] = 1
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        cls.run_cli("index")

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def run_cli(cls, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [PYTHON, "-m", "kb", *args],
            cwd=str(PROJECT),
            env=cls.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"命令失败：kb {' '.join(args)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def test_search(self) -> None:
        result = self.run_cli("search", "报销")
        self.assertIn("报销流程.md", result.stdout)

    def test_search_path_priority(self) -> None:
        result = self.run_cli("search", "sub/安装与使用.md", "--json")
        data = json.loads(result.stdout)
        self.assertEqual(len(data), 1, data)
        self.assertTrue(data[0]["rel_path"].endswith("sub/安装与使用.md"))

    def test_search_fallback_does_not_match_single_term(self) -> None:
        result = self.run_cli("search", "安装与使用", "--json")
        data = json.loads(result.stdout)
        names = [item["name"] for item in data]
        self.assertIn("安装与使用.md", names)
        self.assertNotIn("noise.md", names)

    def test_ask(self) -> None:
        result = self.run_cli("ask", "报销流程是什么")
        self.assertIn("报销", result.stdout)
        self.assertIn("来源", result.stdout)

    def test_sensitive_not_indexed(self) -> None:
        result = self.run_cli("search", "SECRET_TOKEN")
        self.assertNotIn(".env", result.stdout)

    def test_large_file_metadata_only(self) -> None:
        result = self.run_cli("status", "--json")
        data = json.loads(result.stdout)
        self.assertGreaterEqual(data["too_large"], 1)
        search = self.run_cli("search", "large")
        self.assertIn("large.txt", search.stdout)

    def test_summarize(self) -> None:
        result = self.run_cli("summarize", str(self.docs))
        self.assertIn("文件：", result.stdout)

    def test_organize_apply_undo(self) -> None:
        plan_result = self.run_cli("organize", str(self.docs), "--plan", "--strategy", "type")
        match = re.search(r"计划：([0-9a-f]+)", plan_result.stdout)
        self.assertIsNotNone(match, plan_result.stdout)
        plan_id = match.group(1)

        apply_result = self.run_cli("organize", str(self.docs), "--apply", plan_id, "--yes")
        self.assertIn('"failed_count": 0', apply_result.stdout)
        moved = self.docs / "文档" / "报销流程.md"
        self.assertTrue(moved.exists(), f"没有移动到：{moved}")

        undo_result = self.run_cli("organize", str(self.docs), "--undo", plan_id)
        self.assertIn('"failed_count": 0', undo_result.stdout)
        self.assertTrue((self.docs / "报销流程.md").exists())

    def test_doctor(self) -> None:
        result = self.run_cli("doctor", "--json")
        checks = json.loads(result.stdout)
        self.assertTrue(any(item["name"] == "Python" for item in checks))

    def test_stop_without_runtime(self) -> None:
        result = self.run_cli("stop")
        self.assertIn("没有运行中的服务", result.stdout)

    def test_index_without_scope_non_interactive(self) -> None:
        home = self.tmp / "empty-home"
        env = {**os.environ, "KB_HOME": str(home)}
        subprocess.run(
            [PYTHON, "-m", "kb", "init"],
            cwd=str(PROJECT), env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
        )
        result = subprocess.run(
            [PYTHON, "-m", "kb", "index"],
            cwd=str(PROJECT), env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("还没有配置索引范围", result.stdout + result.stderr)

    def test_logs_list(self) -> None:
        log = self.home / "logs" / "mcp.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("日志测试\n", encoding="utf-8")
        result = self.run_cli("logs")
        self.assertIn("日志目录", result.stdout)
        self.assertIn("mcp", result.stdout)

    def test_logs_tail(self) -> None:
        log = self.home / "logs" / "mcp.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("第一行\n第二行\n最后一行\n", encoding="utf-8")
        result = self.run_cli("logs", "mcp", "--lines", "2")
        self.assertIn("最后一行", result.stdout)
        self.assertNotIn("第一行", result.stdout)

    def test_logs_unknown_name(self) -> None:
        result = self.run_cli("logs", "not-a-log", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("没有这个日志", result.stdout)

    def test_logs_clear(self) -> None:
        log = self.home / "logs" / "watch.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("旧内容\n", encoding="utf-8")
        result = self.run_cli("logs", "watch", "--clear")
        self.assertIn("已清空", result.stdout)
        self.assertEqual(log.read_text(encoding="utf-8"), "")

    def test_privacy_scan_handles_permission(self) -> None:
        from kb.privacy import scan

        root = self.tmp / "scan"
        fake_secret = "0123456789" + "abcdef" + "fedcba"
        (root / "locked").mkdir(parents=True, exist_ok=True)
        (root / "secret.txt").write_text(
            f'app_secret = "{fake_secret}"\n', encoding="utf-8"
        )
        (root / "locked" / "inside.txt").write_text("x\n", encoding="utf-8")
        os.chmod(root / "locked", 0)
        try:
            findings = scan(root)
        finally:
            os.chmod(root / "locked", 0o755)
        self.assertTrue(any(item["pattern"] == "app_secret" for item in findings))

    def test_setup_check_json(self) -> None:
        result = self.run_cli("setup", "--check", "--json", check=False)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        data = json.loads(result.stdout)
        for key in ("permissions", "doubao", "feishu", "index", "services", "paths"):
            self.assertIn(key, data)
        self.assertIsInstance(data["permissions"]["ok"], bool)
        self.assertIsInstance(data["doubao"]["installed"], bool)

    def test_setup_check_reports_unconfigured_feishu(self) -> None:
        home = self.tmp / "setup-home"
        env = {**os.environ, "KB_HOME": str(home)}
        result = subprocess.run(
            [PYTHON, "-m", "kb", "setup", "--check", "--json"],
            cwd=str(PROJECT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["feishu"]["configured"])
        self.assertEqual(data["index"]["roots"], [])

    def test_setup_check_is_read_only(self) -> None:
        home = self.tmp / "readonly-home"
        env = {**os.environ, "KB_HOME": str(home)}
        subprocess.run(
            [PYTHON, "-m", "kb", "setup", "--check", "--json"],
            cwd=str(PROJECT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        self.assertFalse((home / "config" / "config.json").exists())
        self.assertFalse((home / "data" / "kb.db").exists())

    def test_path_access_denied(self) -> None:
        from kb.setup_wizard import path_access

        locked = self.tmp / "locked-perm"
        locked.mkdir(parents=True, exist_ok=True)
        (locked / "x.txt").write_text("x", encoding="utf-8")
        os.chmod(locked, 0)
        try:
            self.assertEqual(path_access(locked)["status"], "denied")
        finally:
            os.chmod(locked, 0o755)

    @unittest.skipUnless(sys.platform == "darwin", "菜单栏仅 macOS")
    def test_tray_check(self) -> None:
        result = self.run_cli("tray", "--check")
        self.assertIn("rumps", result.stdout)
        self.assertIn("服务", result.stdout)

    def test_feishu_router(self) -> None:
        from kb.feishu_bot import route_message
        self.assertEqual(route_message("找 报销"), ("search", "报销"))
        self.assertEqual(route_message("帮我找一下报价单"), ("search", "报价单"))
        self.assertEqual(route_message("总结 /tmp/x"), ("summarize", "/tmp/x"))
        self.assertEqual(route_message("整理 /tmp/x"), ("plan", "/tmp/x"))
        self.assertEqual(route_message("整理图片 /tmp/x"), ("plan_image", "/tmp/x"))
        self.assertEqual(route_message("确认整理 abc123"), ("apply", "abc123"))
        self.assertEqual(route_message("撤销 abc123"), ("undo", "abc123"))
        self.assertEqual(route_message("报销流程是什么"), ("ask", "报销流程是什么"))


if __name__ == "__main__":
    unittest.main()
