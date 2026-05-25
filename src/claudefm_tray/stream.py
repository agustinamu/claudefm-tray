from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class StreamInfo:
    title: str
    hls_url: str
    viewers: int | None
    release_ts: int | None


def _run_ytdlp(args: list[str], url: str, timeout: int = 30) -> list[str]:
    cmd = ["yt-dlp", "--no-warnings", *args, url]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip().split("\n")


def resolve_stream(url: str) -> StreamInfo:
    parts = _run_ytdlp(
        [
            "-f", "bestaudio/worst",
            "--print", "%(title)s",
            "--print", "urls",
            "--print", "%(concurrent_view_count)s",
            "--print", "%(release_timestamp)s",
        ],
        url=url,
    )
    if len(parts) < 4:
        raise RuntimeError("yt-dlp returned incomplete metadata")
    title, hls, viewers, ts = parts[:4]
    return StreamInfo(
        title=title,
        hls_url=hls,
        viewers=None if viewers in ("", "NA") else int(viewers),
        release_ts=None if ts in ("", "NA") else int(ts),
    )


def poll_viewers(url: str) -> int | None:
    try:
        parts = _run_ytdlp(["--print", "%(concurrent_view_count)s"], url=url, timeout=15)
    except Exception as e:
        log.warning("poll_viewers failed: %s", e)
        return None
    raw = parts[0] if parts else ""
    return None if raw in ("", "NA") else int(raw)


class MpvController:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def start(self, hls_url: str, title: str) -> None:
        if self.is_alive():
            return
        self.socket_path.unlink(missing_ok=True)
        cmd = [
            "mpv",
            "--no-video",
            "--volume=100",
            "--no-input-terminal",
            "--really-quiet",
            f"--input-ipc-server={self.socket_path}",
            f"--force-media-title={title}",
            hls_url,
        ]
        log.info("starting mpv")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(50):
            if self.socket_path.exists():
                return
            time.sleep(0.1)
        log.warning("mpv IPC socket did not appear in 5s")

    def stop(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        except Exception as e:
            log.warning("mpv stop: %s", e)
        self.proc = None
        self.socket_path.unlink(missing_ok=True)

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _send(self, payload: dict) -> dict | None:
        if not self.socket_path.exists():
            return None
        with self._lock:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(2)
                    s.connect(str(self.socket_path))
                    s.sendall((json.dumps(payload) + "\n").encode())
                    data = s.recv(4096).decode()
                if not data:
                    return None
                return json.loads(data.splitlines()[0])
            except (OSError, json.JSONDecodeError) as e:
                log.warning("mpv IPC failed: %s", e)
                return None

    def toggle_pause(self) -> None:
        self._send({"command": ["cycle", "pause"]})

    def is_paused(self) -> bool:
        r = self._send({"command": ["get_property", "pause"]})
        return bool(r and r.get("data"))
