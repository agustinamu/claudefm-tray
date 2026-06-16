from __future__ import annotations

import os
from pathlib import Path


def claudefm_url_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claudefm" / "url"


def read_url() -> str:
    p = claudefm_url_path()
    if not p.exists():
        raise SystemExit(
            f"no claudefm URL configured at {p} — write the YouTube live URL there"
        )
    return p.read_text().strip()


def write_url(url: str) -> None:
    p = claudefm_url_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(url.strip() + "\n")


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{os.getuid()}"
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def runtime_socket() -> Path:
    return runtime_dir() / "claudefm-tray.sock"


def runtime_lock() -> Path:
    return runtime_dir() / "claudefm-tray.lock"
