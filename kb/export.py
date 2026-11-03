from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import __version__
from .config import HOME, config_path, db_path, load_config
from .db import connect, list_roots

EXCLUDE_DIRS = {
    ".venv", "venv", "__pycache__", ".git", "data", "logs",
    ".pytest_cache", "dist", "personal_kb.egg-info",
}
EXCLUDE_FILES = {
    "config/config.json",
    "config/feishu.json",
    "config/organize_rules.json",
}


def _project_files(root: Path, include_data: bool, skip: Path | None = None) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if skip is not None and path == skip:
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == "config" and not path.name.endswith(".example.json"):
            continue
        if ".bak" in path.name or path.name in {".DS_Store", "Thumbs.db"}:
            continue
        if any(part in EXCLUDE_DIRS for part in relative.parts) and not (
            include_data and relative.parts[0] == "data"
        ):
            continue
        if str(relative).replace("\\", "/") in EXCLUDE_FILES:
            continue
        if relative.suffix.lower() in {".db", ".db-wal", ".db-shm", ".pyc"}:
            continue
        yield path


def export_bundle(output: str | Path, *, mode: str = "share", prefix: str = "") -> dict:
    root = HOME.resolve()
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    include_data = mode == "migrate"
    prefix = prefix.strip("/")

    def arcname(relative: str) -> str:
        return f"{prefix}/{relative}" if prefix else relative

    manifest = {
        "name": "personal-kb",
        "version": __version__,
        "mode": mode,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "roots": [],
    }

    if include_data:
        conn = connect(db_path())
        try:
            manifest["roots"] = [dict(row) for row in list_roots(conn)]
        finally:
            conn.close()

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in _project_files(root, include_data=include_data, skip=output_path):
            archive.write(path, arcname(path.relative_to(root).as_posix()))
        if include_data:
            for extra in (db_path(), config_path()):
                if extra.exists():
                    archive.write(extra, arcname(extra.relative_to(root).as_posix()))
        archive.writestr(
            arcname("manifest.json"),
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    return {
        "output": str(output_path),
        "mode": mode,
        "bytes": output_path.stat().st_size,
        "roots": manifest["roots"],
    }


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = []
    for member in archive.infolist():
        target = Path(member.filename)
        if target.is_absolute() or ".." in target.parts:
            raise RuntimeError(f"压缩包包含不安全路径：{member.filename}")
        members.append(member)
    return members


def import_bundle(source: str | Path, *, force: bool = False) -> dict:
    source_path = Path(source).expanduser().resolve()
    root = HOME.resolve()
    restored: list[str] = []
    skipped: list[str] = []

    with zipfile.ZipFile(source_path) as archive:
        members = _safe_members(archive)
        names = {member.filename for member in members}
        manifest = {}
        if "manifest.json" in names:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        for member in members:
            relative = Path(member.filename)
            destination = (root / relative).resolve()
            if root not in destination.parents and destination != root:
                raise RuntimeError(f"压缩包路径越界：{member.filename}")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if destination.exists() and not force:
                skipped.append(member.filename)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as handle:
                destination.write_bytes(handle.read())
            restored.append(member.filename)

    return {
        "source": str(source_path),
        "restored": len(restored),
        "skipped": skipped,
        "manifest": manifest,
    }
