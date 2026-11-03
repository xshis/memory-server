from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


_SCHEMA_READY: set[str] = set()


def _database_key(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        return str(row[2]) if row and row[2] else ""
    except sqlite3.Error:
        return ""


def connect(path: str | Path, check_same_thread: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    key = _database_key(conn)
    if key and key in _SCHEMA_READY:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS roots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_id INTEGER NOT NULL,
            path TEXT NOT NULL UNIQUE,
            rel_path TEXT NOT NULL,
            name TEXT NOT NULL,
            ext TEXT NOT NULL DEFAULT '',
            size INTEGER NOT NULL DEFAULT 0,
            mtime REAL NOT NULL DEFAULT 0,
            ctime REAL NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            content_status TEXT NOT NULL DEFAULT 'unknown',
            content_chars INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            last_seen_run TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(root_id) REFERENCES roots(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_files_root ON files(root_id);
        CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
        CREATE INDEX IF NOT EXISTS idx_files_ext ON files(ext);
        CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime);

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);

        CREATE TABLE IF NOT EXISTS feishu_events (
            event_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );
        """
    )
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS files_fts "
            "USING fts5(name, rel_path, content, tokenize='trigram')"
        )
    except sqlite3.OperationalError:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS files_fts "
            "USING fts5(name, rel_path, content, tokenize='unicode61')"
        )
    conn.commit()
    if key:
        _SCHEMA_READY.add(key)


def add_root(conn: sqlite3.Connection, path: str | Path, label: str = "") -> int:
    resolved = str(Path(path).expanduser().resolve())
    conn.execute(
        "INSERT OR IGNORE INTO roots(path, label, created_at) VALUES(?, ?, ?)",
        (resolved, label or Path(resolved).name, now_iso()),
    )
    row = conn.execute("SELECT id FROM roots WHERE path = ?", (resolved,)).fetchone()
    conn.commit()
    return int(row["id"])


def remove_root(conn: sqlite3.Connection, path_or_id: str) -> int:
    row = None
    if path_or_id.isdigit():
        row = conn.execute("SELECT id FROM roots WHERE id = ?", (int(path_or_id),)).fetchone()
    if row is None:
        resolved = str(Path(path_or_id).expanduser().resolve())
        row = conn.execute("SELECT id FROM roots WHERE path = ?", (resolved,)).fetchone()
    if row is None:
        return 0
    root_id = int(row["id"])
    for file_row in conn.execute("SELECT id FROM files WHERE root_id = ?", (root_id,)).fetchall():
        _delete_file_row(conn, int(file_row["id"]))
    conn.execute("DELETE FROM roots WHERE id = ?", (root_id,))
    conn.commit()
    return 1


def list_roots(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM roots ORDER BY id").fetchall()


def root_for_path(conn: sqlite3.Connection, path: str | Path) -> sqlite3.Row | None:
    resolved = Path(path).expanduser().resolve()
    best = None
    best_len = -1
    for row in list_roots(conn):
        root = Path(row["path"]).expanduser().resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if len(str(root)) > best_len:
            best = row
            best_len = len(str(root))
    return best


def ensure_allowed_path(conn: sqlite3.Connection, path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if root_for_path(conn, resolved) is None:
        raise PermissionError(
            f"路径不在已配置的索引范围内：{resolved}\n"
            "先用 `kb scope --add <路径>` 把它加入范围。"
        )
    return resolved


def _delete_file_row(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute("DELETE FROM files_fts WHERE rowid = ?", (file_id,))
    conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))


def delete_file_by_path(conn: sqlite3.Connection, path: str | Path) -> bool:
    resolved = str(Path(path).expanduser().resolve())
    row = conn.execute("SELECT id FROM files WHERE path = ?", (resolved,)).fetchone()
    if row is None:
        return False
    _delete_file_row(conn, int(row["id"]))
    conn.commit()
    return True


def delete_file_by_id(conn: sqlite3.Connection, file_id: int) -> bool:
    row = conn.execute("SELECT id FROM files WHERE id = ?", (int(file_id),)).fetchone()
    if row is None:
        return False
    _delete_file_row(conn, int(row["id"]))
    conn.commit()
    return True


def delete_under_path(conn: sqlite3.Connection, path: str | Path) -> int:
    """删除某个目录（或文件）下的全部索引记录，返回删除行数。"""
    resolved = str(Path(path).expanduser().resolve())
    prefix = resolved + os.sep
    rows = conn.execute(
        "SELECT id FROM files WHERE path = ? OR substr(path, 1, ?) = ?",
        (resolved, len(prefix), prefix),
    ).fetchall()
    for row in rows:
        _delete_file_row(conn, int(row[0]))
    conn.commit()
    return len(rows)


def get_file_by_path(conn: sqlite3.Connection, path: str | Path) -> sqlite3.Row | None:
    resolved = str(Path(path).expanduser().resolve())
    return conn.execute("SELECT * FROM files WHERE path = ?", (resolved,)).fetchone()


def upsert_file(
    conn: sqlite3.Connection,
    *,
    root_id: int,
    path: str | Path,
    rel_path: str,
    name: str,
    ext: str,
    size: int,
    mtime: float,
    ctime: float,
    content: str,
    content_status: str,
    error: str,
    sha256: str,
    chunks: Iterable[str],
    last_seen_run: str,
) -> int:
    resolved = str(Path(path).expanduser().resolve())
    row = conn.execute("SELECT id FROM files WHERE path = ?", (resolved,)).fetchone()
    values = (
        root_id, resolved, rel_path, name, ext, size, mtime, ctime, sha256,
        content, content_status, len(content), error, last_seen_run, now_iso(),
    )
    if row is None:
        cur = conn.execute(
            """
            INSERT INTO files(root_id, path, rel_path, name, ext, size, mtime, ctime,
                              sha256, content, content_status, content_chars, error,
                              last_seen_run, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            values,
        )
        file_id = int(cur.lastrowid)
    else:
        file_id = int(row["id"])
        conn.execute(
            """
            UPDATE files SET root_id=?, path=?, rel_path=?, name=?, ext=?, size=?, mtime=?,
                ctime=?, sha256=?, content=?, content_status=?, content_chars=?, error=?,
                last_seen_run=?, updated_at=?
            WHERE id=?
            """,
            values + (file_id,),
        )
        conn.execute("DELETE FROM files_fts WHERE rowid = ?", (file_id,))
        conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))

    conn.execute(
        "INSERT INTO files_fts(rowid, name, rel_path, content) VALUES(?, ?, ?, ?)",
        (file_id, name, rel_path, content),
    )
    for index, chunk in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks(file_id, chunk_index, text) VALUES(?, ?, ?)",
            (file_id, index, chunk),
        )
    conn.commit()
    return file_id


def delete_missing(conn: sqlite3.Connection, root_id: int, run_id: str) -> int:
    rows = conn.execute(
        "SELECT id FROM files WHERE root_id = ? AND last_seen_run <> ?",
        (root_id, run_id),
    ).fetchall()
    for row in rows:
        _delete_file_row(conn, int(row["id"]))
    conn.commit()
    return len(rows)


def search_rows(
    conn: sqlite3.Connection,
    match_query: str,
    *,
    limit: int,
    ext: str = "",
    root_id: int | None = None,
) -> list[sqlite3.Row]:
    sql = """
        SELECT f.*, bm25(files_fts) AS rank,
               snippet(files_fts, 2, '[', ']', '…', 24) AS snippet
        FROM files_fts
        JOIN files f ON f.id = files_fts.rowid
        WHERE files_fts MATCH ?
    """
    params: list[Any] = [match_query]
    if ext:
        sql += " AND f.ext = ?"
        params.append(ext.lower())
    if root_id is not None:
        sql += " AND f.root_id = ?"
        params.append(root_id)
    sql += " ORDER BY rank ASC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def search_like(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    ext: str = "",
    root_id: int | None = None,
) -> list[sqlite3.Row]:
    like = f"%{query}%"
    sql = """
        SELECT f.*, 0.0 AS rank,
               substr(f.content, 1, 240) AS snippet
        FROM files f
        WHERE (f.name LIKE ? OR f.rel_path LIKE ? OR f.content LIKE ?)
    """
    params: list[Any] = [like, like, like]
    if ext:
        sql += " AND f.ext = ?"
        params.append(ext.lower())
    if root_id is not None:
        sql += " AND f.root_id = ?"
        params.append(root_id)
    sql += " ORDER BY f.mtime DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def make_fts_query(query: str) -> str:
    terms = [term for term in re.split(r"\s+", query.strip()) if term]
    if not terms:
        return ""
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    totals = conn.execute(
        """
        SELECT COUNT(*) AS files,
               COALESCE(SUM(size), 0) AS bytes,
               SUM(CASE WHEN content_status = 'indexed' THEN 1 ELSE 0 END) AS indexed,
               SUM(CASE WHEN content_status = 'too_large' THEN 1 ELSE 0 END) AS too_large,
               SUM(CASE WHEN content_status = 'unsupported' THEN 1 ELSE 0 END) AS unsupported,
               SUM(CASE WHEN content_status = 'error' THEN 1 ELSE 0 END) AS errors
        FROM files
        """
    ).fetchone()
    roots = []
    for row in conn.execute(
        """
        SELECT r.id, r.path, r.label, COUNT(f.id) AS files,
               COALESCE(SUM(f.size), 0) AS bytes,
               MAX(f.updated_at) AS last_update
        FROM roots r LEFT JOIN files f ON f.root_id = r.id
        GROUP BY r.id ORDER BY r.id
        """
    ).fetchall():
        roots.append(dict(row))
    return {
        "files": int(totals["files"] or 0),
        "bytes": int(totals["bytes"] or 0),
        "indexed": int(totals["indexed"] or 0),
        "too_large": int(totals["too_large"] or 0),
        "unsupported": int(totals["unsupported"] or 0),
        "errors": int(totals["errors"] or 0),
        "roots": roots,
    }
