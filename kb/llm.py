from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG, config_path, load_config, save_config

DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_HOST = "http://127.0.0.1:11434"


def host() -> str:
    return os.environ.get("OLLAMA_HOST", DEFAULT_HOST).rstrip("/")


def _binary() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "ollama"
    return str(candidate) if candidate.exists() else None


def installed() -> bool:
    return _binary() is not None


def _request(path: str, *, timeout: float = 3, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{host()}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def status() -> dict[str, Any]:
    config = load_config() if config_path().exists() else DEFAULT_CONFIG
    result: dict[str, Any] = {
        "installed": installed(),
        "running": False,
        "host": host(),
        "models": [],
        "error": "",
        "mode": str(config.get("models", {}).get("mode", "none")),
        "configured_model": model_name(config),
    }
    try:
        data = _request("/api/tags", timeout=2)
        result["running"] = True
        result["models"] = [str(item.get("name", "")) for item in data.get("models", [])]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        result["error"] = str(exc)
    return result


def model_name(config: dict[str, Any] | None = None) -> str:
    config = config or load_config()
    return str(config.get("models", {}).get("local", {}).get("chat", DEFAULT_MODEL)).strip()


def model_available(model: str, status_data: dict[str, Any] | None = None) -> bool:
    models = (status_data or status()).get("models", [])
    names = {str(name) for name in models}
    return model in names or f"{model}:latest" in names or any(
        str(name).split(":", 1)[0] == model for name in names
    )


def start_server(timeout: float = 15) -> bool:
    if not installed():
        return False
    if status()["running"]:
        return True
    try:
        subprocess.Popen(
            [_binary() or "ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if status()["running"]:
            return True
        time.sleep(0.5)
    return False


def install_ollama() -> bool:
    if installed():
        return True
    brew = shutil.which("brew")
    if not brew:
        return False
    result = subprocess.run([brew, "install", "ollama"], check=False)
    return result.returncode == 0 and installed()


def pull(model: str) -> bool:
    if not start_server():
        return False
    try:
        result = subprocess.run([_binary() or "ollama", "pull", model], check=False)
    except OSError:
        return False
    return result.returncode == 0 and model_available(model)


def chat(model: str, messages: list[dict[str, str]], *, timeout: float = 120) -> str:
    config = load_config() if config_path().exists() else DEFAULT_CONFIG
    local = config.get("models", {}).get("local", {})
    options = {
        "temperature": 0.2,
        "num_gpu": int(local.get("num_gpu", 0)),
        "num_thread": int(local.get("num_thread", os.cpu_count() or 4)),
        "num_ctx": int(local.get("num_ctx", 3072)),
        "num_predict": int(local.get("num_predict", 320)),
    }
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": str(local.get("keep_alive", "24h")),
        "options": options,
    }
    data = _request("/api/chat", timeout=timeout, payload=payload)
    return str(data.get("message", {}).get("content", "")).strip()


def warmup(model: str) -> bool:
    try:
        chat(model, [{"role": "user", "content": "只回复 OK"}], timeout=90)
        return True
    except Exception:  # noqa: BLE001
        return False


def warmup_async(model: str) -> None:
    import threading

    threading.Thread(target=warmup, args=(model,), daemon=True).start()


def _backup_config() -> None:
    path = config_path()
    if not path.exists():
        return
    backup = path.with_name(f"config.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        shutil.copy2(path, backup)
    except OSError:
        pass


def enable(model: str | None = None) -> dict[str, Any]:
    config = load_config()
    selected = (model or model_name(config)).strip() or DEFAULT_MODEL
    _backup_config()
    models = config.setdefault("models", {})
    models.setdefault("local", {})["chat"] = selected
    models["mode"] = "ollama"
    save_config(config)
    return {"mode": "ollama", "model": selected}


def disable() -> dict[str, Any]:
    config = load_config()
    _backup_config()
    config.setdefault("models", {})["mode"] = "none"
    save_config(config)
    return {"mode": "none"}
