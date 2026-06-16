from __future__ import annotations

import argparse
import fcntl
import os
import sys
from pathlib import Path

from .config import runtime_lock
from .tray import run


def _acquire_single_instance_lock(path: Path):
    """Hold an exclusive flock on `path` for the life of the process.

    Returns the open file object so the caller can keep a reference (the
    lock is released when the descriptor is closed). Exits 0 if another
    instance already holds the lock — a fresh launch should be a no-op,
    not an error.
    """
    f = open(path, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.stderr.write(f"claudefm-tray already running (lock: {path})\n")
        f.close()
        sys.exit(0)
    f.truncate(0)
    f.write(f"{os.getpid()}\n")
    f.flush()
    return f


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="claudefm-tray",
        description="Tray indicator for the claudeFM YouTube radio.",
    )
    parser.add_argument(
        "--paused",
        action="store_true",
        help="Start with playback paused (useful for login autostart).",
    )
    args = parser.parse_args()

    # Keep a reference so the lock is held for the whole process lifetime.
    _lock = _acquire_single_instance_lock(runtime_lock())  # noqa: F841

    run(start_paused=args.paused)


if __name__ == "__main__":
    main()
