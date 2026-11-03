from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterator

SECRET_PATTERNS = [
    ("app_secret", re.compile(r"app_secret\s*[:=]\s*['\"]?[A-Za-z0-9_-]{16,}", re.I)),
    ("tenant_access_token", re.compile(r"tenant_access_token\s*[:=]\s*['\"]?[A-Za-z0-9._-]{20,}", re.I)),
    ("user_access_token", re.compile(r"user_access_token\s*[:=]\s*['\"]?[A-Za-z0-9._-]{20,}", re.I)),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.I)),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}")),
    ("feishu_app_id", re.compile(r"cli_[a-z0-9]{12,}")),
]
PERSONAL_PATTERNS = [
    ("绝对用户路径", re.compile(r"/Users/[A-Za-z0-9._-]+")),
    ("Windows 用户路径", re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
]
EXCLUDE_DIRS = {
    ".venv", "venv", "__pycache__", ".git", "data", "logs",
    ".idea", ".vscode", "dist", "personal_kb.egg-info",
}
EXCLUDE_FILES = {
    "README.md",
    "config/config.example.json",
    "config/feishu.example.json",
    "config/config.json",
    "config/feishu.json",
    "kb/privacy.py",
}
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    ".sh", ".ps1", ".bat", ".cmd", ".html", ".css", ".js", ".ts",
}


def _redact(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return value[:4] + "…" + value[-2:]


def _walk_files(root: Path) -> Iterator[Path]:
    """遍历文本文件；无权限目录和符号链接直接跳过，不中断扫描。"""

    def ignore_error(_error: OSError) -> None:
        return

    for current, dirnames, filenames in os.walk(
        root, topdown=True, onerror=ignore_error, followlinks=False
    ):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            yield Path(current) / name


def scan(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    root = root.resolve()
    for path in _walk_files(root):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root)
        except OSError:
            continue
        if relative.parts and relative.parts[0] == "config" and not path.name.endswith(".example.json"):
            continue
        if ".bak" in path.name or path.name in {".DS_Store", "Thumbs.db"}:
            continue
        if any(part in EXCLUDE_DIRS for part in relative.parts):
            continue
        if str(relative).replace("\\", "/") in EXCLUDE_FILES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for name, pattern in SECRET_PATTERNS:
                for match in pattern.finditer(line):
                    findings.append({
                        "kind": "secret",
                        "pattern": name,
                        "file": str(relative),
                        "line": line_number,
                        "excerpt": _redact(match.group(0)),
                    })
            for name, pattern in PERSONAL_PATTERNS:
                for match in pattern.finditer(line):
                    findings.append({
                        "kind": "personal",
                        "pattern": name,
                        "file": str(relative),
                        "line": line_number,
                        "excerpt": _redact(match.group(0)),
                    })
    return findings
