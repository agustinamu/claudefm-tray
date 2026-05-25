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


def runtime_socket() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{os.getuid()}"
    Path(base).mkdir(parents=True, exist_ok=True)
    return Path(base) / "claudefm-tray.sock"
