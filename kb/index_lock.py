from __future__ import annotations

import os
from pathlib import Path


class IndexLock:
    """跨进程的 kb index 锁；同一时间只允许一个索引进程。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.handle = None  # type: ignore[assignment]
        self.acquired = False

    def acquire(self, blocking: bool = False) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            handle.seek(0)
            if handle.read(1) == "":
                handle.write("0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                flags = fcntl.LOCK_EX
                if not blocking:
                    flags |= fcntl.LOCK_NB
                fcntl.flock(handle.fileno(), flags)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
        self.handle = handle
        self.acquired = True
        return True

    def holder_pid(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def release(self) -> None:
        if not self.handle:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                self.handle.close()
            finally:
                self.handle = None  # type: ignore[assignment]
                self.acquired = False

    def __enter__(self) -> "IndexLock":
        if not self.acquire():
            raise RuntimeError("已有索引进程在运行")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
