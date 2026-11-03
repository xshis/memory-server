from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def runtime_path() -> Path:
    from .config import HOME
    return HOME / "data" / "runtime.json"


def write_runtime(pids: list[int], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    path = runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "pids": [int(pid) for pid in pids if int(pid) > 0],
        "parent_pid": os.getpid(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if extra:
        data.update(extra)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return data


def read_runtime() -> dict[str, Any]:
    path = runtime_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def clear_runtime(owner_pid: int | None = None) -> None:
    if owner_pid is not None:
        data = read_runtime()
        recorded = data.get("parent_pid")
        if recorded is not None and int(recorded) != int(owner_pid):
            return
    try:
        runtime_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def port_in_use(host: str, port: int) -> bool:
    bind_host = host if host not in {"", "0.0.0.0", "::"} else "0.0.0.0"
    family = socket.AF_INET6 if ":" in bind_host and bind_host != "0.0.0.0" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
            return False
        except OSError:
            return True


def process_command(pid: int) -> str:
    if pid <= 0:
        return ""
    if os.name == "nt":
        try:
            output = subprocess.run(
                ["wmic", "process", "where", f"processid={pid}", "get", "commandline"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            return " ".join(lines[1:]) if len(lines) > 1 else ""
        except Exception:
            try:
                output = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                    capture_output=True, text=True, timeout=5,
                ).stdout
                return output.strip()
            except Exception:
                return ""
    try:
        return subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return ""


def is_kb_process(pid: int) -> bool:
    command = process_command(pid).lower()
    if not command:
        return False
    return (
        "-m kb" in command
        or "personal-kb" in command
        or "kb start" in command
        or "kb mcp" in command
        or "kb watch" in command
        or "kb feishu" in command
    )


def process_cwd(pid: int) -> str:
    if os.name == "nt":
        return ""
    try:
        output = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return ""
    for line in output.splitlines():
        if line.startswith("n"):
            return line[1:].strip()
    return ""


def find_start_pids() -> list[int]:
    """Find `kb start` supervisors belonging to this project (best effort)."""
    from .config import HOME

    markers = {
        str(HOME).lower(),
        str(Path(__file__).resolve().parent.parent).lower(),
        str(Path(sys.executable).resolve().parent).lower(),
    }
    if os.name == "nt":
        return []
    try:
        output = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    pids = []
    for line in output.splitlines():
        line = line.strip()
        if not line or ("-m kb start" not in line and "kb start" not in line):
            continue
        pid_text, _, command = line.partition(" ")
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == os.getpid():
            continue
        tokens = command.split()
        first = tokens[0].lower() if tokens else ""
        if os.path.basename(first) in {
            "bash", "sh", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh",
        }:
            continue
        if not re.search(r"(?:^|[\s/])kb\s+start(?:\s|$)", command, re.I):
            continue
        command_lower = command.lower()
        cwd_lower = process_cwd(pid).lower()
        if not any(
            (marker and marker in command_lower) or (marker and marker in cwd_lower)
            for marker in markers
        ):
            continue
        pids.append(pid)
    return pids


def stop_starters() -> list[int]:
    stopped = []
    for pid in find_start_pids():
        if terminate_pid(pid):
            stopped.append(pid)
    return stopped


def port_pids(port: int) -> list[int]:
    if os.name == "nt":
        try:
            output = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            ).stdout
        except Exception:
            return []
        pids = set()
        for line in output.splitlines():
            if f":{port}" not in line or "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(int(parts[-1]))
        return sorted(pids)
    try:
        output = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    pids = []
    for token in re.split(r"\s+", output.strip()):
        if token.isdigit():
            pids.append(int(token))
    return sorted(set(pids))


def stop_ports(
    ports: list[int],
    host: str = "127.0.0.1",
    *,
    only_kb: bool = True,
) -> dict[str, Any]:
    pids: set[int] = set()
    for port in ports:
        pids.update(port_pids(port))
    stopped = []
    failed = []
    foreign = []
    for pid in sorted(pids):
        if pid == os.getpid():
            continue
        if only_kb and not is_kb_process(pid):
            foreign.append(pid)
            continue
        if terminate_pid(pid):
            stopped.append(pid)
        else:
            failed.append(pid)
    return {
        "stopped": stopped,
        "failed": failed,
        "foreign": foreign,
        "pids": sorted(pids),
    }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def terminate_pid(pid: int, timeout: float = 3.0) -> bool:
    if pid <= 0:
        return False
    if not _pid_alive(pid):
        return True
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            return True
        except Exception:
            return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.15)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return not _pid_alive(pid)


def stop_all(exclude: set[int] | None = None) -> dict[str, Any]:
    data = read_runtime()
    pids: list[int] = []
    if isinstance(data, dict):
        parent = data.get("parent_pid")
        if parent:
            try:
                pids.append(int(parent))
            except (TypeError, ValueError):
                pass
        for pid in data.get("pids", []) or []:
            try:
                pids.append(int(pid))
            except (TypeError, ValueError):
                continue
    pids = list(dict.fromkeys(pids))
    exclude = exclude or set()
    stopped: list[int] = []
    failed: list[int] = []
    for pid_int in pids:
        if pid_int in exclude or pid_int == os.getpid():
            continue
        if terminate_pid(pid_int):
            stopped.append(pid_int)
        else:
            failed.append(pid_int)
    clear_runtime()
    return {
        "stopped": stopped,
        "failed": failed,
        "message": "没有运行中的服务" if not pids else "已关闭",
    }
