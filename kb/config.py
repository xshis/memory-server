from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "knowledge-base"
DEFAULT_CONFIG: dict[str, Any] = {
    "app": {"name": "知识库助手"},
    "assistant": {
        "name": "小助手",
        "persona": "你的专属小助手，说话简短、自然，像朋友聊天",
    },
    "index": {
        "max_file_size_mb": 20,
        "max_content_chars": 2_000_000,
        "exclude_dirs": [
            ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
            ".idea", ".vscode", ".Trash", "$RECYCLE.BIN", "System Volume Information",
            "Windows", "Program Files", "Program Files (x86)", "ProgramData",
            "Library", "AppData", "Containers", "Caches", "Group Containers",
        ],
        "exclude_globs": [
            "*.tmp", "*.temp", "*.part", "*.crdownload", "*.download",
            "~$*", ".DS_Store", "Thumbs.db", "desktop.ini",
        ],
        "sensitive_globs": [
            ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx",
            "id_rsa", "id_ed25519", "credentials*", "Cookies", "Login Data", "Web Data",
        ],
        "text_exts": [
            ".md", ".markdown", ".txt", ".text", ".log",
            ".json", ".jsonl", ".csv", ".tsv", ".xml", ".yaml", ".yml", ".toml",
            ".ini", ".cfg", ".conf", ".html", ".htm", ".css", ".scss", ".less",
            ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
            ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs",
            ".rb", ".php", ".swift", ".sql", ".sh", ".bash", ".zsh",
            ".ps1", ".bat", ".cmd", ".r", ".lua", ".pl",
        ],
    },
    "search": {"default_limit": 20},
    "mcp": {"host": "127.0.0.1", "port": 8766},
    "watch": {"interval_seconds": 30},
    "models": {
        "mode": "none",
        "local": {
            "chat": "qwen2.5:3b",
            "embed": "nomic-embed-text",
            "num_gpu": 0,
            "num_thread": 8,
            "num_ctx": 1536,
            "num_predict": 180,
            "keep_alive": "24h",
            "context_chars": 1200,
            "max_sources": 2,
            "cache_seconds": 600,
            "answer_without_context": False,
        },
        "cloud": {"enabled": False},
    },
}


def _looks_like_home(path: Path) -> bool:
    return (path / "config").is_dir() and (path / "kb").is_dir()


def find_home() -> Path:
    """Locate the portable app directory.

    Priority: KB_HOME, the folder containing the launcher, current directory,
    then a per-user fallback.
    """
    env = os.environ.get("KB_HOME")
    if env:
        return Path(env).expanduser().resolve()

    candidates: list[Path] = []
    try:
        argv0 = Path(sys.argv[0]).resolve()
        candidates.extend([argv0.parent, argv0.parent.parent])
    except Exception:
        pass
    candidates.append(Path.cwd())
    try:
        candidates.append(Path(__file__).resolve().parent.parent)
    except Exception:
        pass

    for candidate in candidates:
        if _looks_like_home(candidate):
            return candidate
    return Path.home() / ".personal-kb"


HOME = find_home()


def ensure_dirs() -> None:
    for path in (HOME, HOME / "config", HOME / "data", HOME / "data" / "plans",
                 HOME / "data" / "undo", HOME / "logs"):
        path.mkdir(parents=True, exist_ok=True)


def config_path() -> Path:
    return HOME / "config" / "config.json"


def db_path() -> Path:
    return HOME / "data" / "kb.db"


def plans_dir() -> Path:
    return HOME / "data" / "plans"


def undo_dir() -> Path:
    return HOME / "data" / "undo"


def logs_dir() -> Path:
    return HOME / "logs"


def _merge_defaults(value: Any, default: Any) -> Any:
    if isinstance(value, dict) and isinstance(default, dict):
        merged = copy.deepcopy(default)
        for key, item in value.items():
            merged[key] = _merge_defaults(item, default.get(key))
        return merged
    return value


def load_config() -> dict[str, Any]:
    ensure_dirs()
    path = config_path()
    if not path.exists():
        example = HOME / "config" / "config.example.json"
        if example.exists():
            data = json.loads(example.read_text(encoding="utf-8"))
        else:
            data = copy.deepcopy(DEFAULT_CONFIG)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return _merge_defaults(data, DEFAULT_CONFIG)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"配置文件读取失败：{path}：{exc}") from exc
    config = _merge_defaults(data, DEFAULT_CONFIG)
    excludes = config.setdefault("index", {}).setdefault("exclude_dirs", [])
    for name in ("Library", "AppData", "Containers", "Caches", "Group Containers"):
        if name not in excludes:
            excludes.append(name)
    return config


def save_config(config: dict[str, Any]) -> None:
    ensure_dirs()
    config_path().write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def feishu_config_path() -> Path:
    return HOME / "config" / "feishu.json"


def load_feishu_config() -> dict[str, Any]:
    ensure_dirs()
    path = feishu_config_path()
    if not path.exists():
        example = HOME / "config" / "feishu.example.json"
        if example.exists():
            data = json.loads(example.read_text(encoding="utf-8"))
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return data
        return {"app_id": "", "app_secret": "", "allow_all": False,
                "allowed_open_ids": [], "allowed_chat_ids": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"飞书配置读取失败：{path}：{exc}") from exc


def feishu_ready() -> bool:
    try:
        config = load_feishu_config()
    except RuntimeError:
        return False
    return bool(
        str(config.get("app_id", "")).strip()
        and str(config.get("app_secret", "")).strip()
    )


def organize_rules_path() -> Path:
    return HOME / "config" / "organize_rules.json"


DEFAULT_ORGANIZE_RULES: dict[str, list[str]] = {
    "财务": ["发票", "报销", "预算", "账单", "税", "付款", "收款", "对账", "工资"],
    "合同": ["合同", "协议", "甲方", "乙方", "条款", "签署", "盖章"],
    "会议": ["会议", "纪要", "议程", "参会", "讨论", "决议"],
    "项目": ["项目", "需求", "排期", "验收", "里程碑", "计划", "进度"],
    "技术": ["接口", "部署", "代码", "数据库", "架构", "运维", "测试", "故障"],
    "人事": ["简历", "招聘", "入职", "离职", "绩效", "考勤", "培训"],
    "个人": ["身份证", "户口", "病历", "保险", "房产", "证书"],
}


def load_organize_rules() -> dict[str, list[str]]:
    path = organize_rules_path()
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_ORGANIZE_RULES, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return dict(DEFAULT_ORGANIZE_RULES)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): [str(x) for x in v] for k, v in data.items() if isinstance(v, list)}
    except (OSError, json.JSONDecodeError):
        pass
    return dict(DEFAULT_ORGANIZE_RULES)
