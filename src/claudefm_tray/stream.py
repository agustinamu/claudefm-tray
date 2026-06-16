from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class StreamInfo:
    title: str
    hls_url: str
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
            "--print", "%(release_timestamp)s",
        ],
        url=url,
    )
    if len(parts) < 3:
        raise RuntimeError("yt-dlp returned incomplete metadata")
    title, hls, ts = parts[:3]
    return StreamInfo(
        title=title,
        hls_url=hls,
        release_ts=None if ts in ("", "NA") else int(ts),
    )


# observe_property IDs — kept stable so the reader can route events by id.
_OBS_VOLUME = 1
_OBS_PAUSE = 2


class MpvController:
    """Drives mpv over its JSON IPC socket.

    Keeps one long-lived socket and a reader thread that dispatches
    property-change events to user-supplied callbacks. There is no polling;
    mpv pushes updates whenever volume/pause change (including the initial
    value, emitted automatically when observe_property is registered).
    """

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        # Callbacks invoked from the reader thread — wrap with GLib.idle_add
        # on the consumer side if you need to touch GTK.
        self.on_volume_change: Callable[[int], None] | None = None
        self.on_pause_change: Callable[[bool], None] | None = None

    def start(
        self,
        hls_url: str,
        title: str,
        volume: int = 100,
        paused: bool = False,
    ) -> None:
        if self.is_alive():
            return
        self._close_socket()
        self.socket_path.unlink(missing_ok=True)
        cmd = [
            "mpv",
            "--no-video",
            f"--volume={volume}",
            "--no-input-terminal",
            "--really-quiet",
            f"--input-ipc-server={self.socket_path}",
            f"--force-media-title={title}",
        ]
        if paused:
            cmd.append("--pause")
        cmd.append(hls_url)
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
                break
            time.sleep(0.1)
        else:
            log.warning("mpv IPC socket did not appear in 5s")
            return
        if not self._open_socket():
            return
        self._start_reader()
        # Subscribe to property changes. mpv emits the current value once at
        # registration, so we don't need a follow-up get_property.
        self._send({"command": ["observe_property", _OBS_VOLUME, "volume"]})
        self._send({"command": ["observe_property", _OBS_PAUSE, "pause"]})

    def stop(self) -> None:
        self._close_socket()
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

    def toggle_pause(self) -> None:
        self._send({"command": ["cycle", "pause"]})

    def add_volume(self, delta: int) -> None:
        self._send({"command": ["add", "volume", delta]})

    def set_volume(self, value: int) -> None:
        value = max(0, min(130, value))
        self._send({"command": ["set_property", "volume", value]})

    def _open_socket(self) -> bool:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(str(self.socket_path))
        except OSError as e:
            log.warning("mpv IPC connect failed: %s", e)
            return False
        self._sock = s
        return True

    def _close_socket(self) -> None:
        s, self._sock = self._sock, None
        if s is None:
            return
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            s.close()
        except OSError:
            pass

    def _start_reader(self) -> None:
        t = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread = t
        t.start()

    def _reader_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        buf = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._dispatch(msg)
        except OSError as e:
            log.info("mpv reader stopped: %s", e)

    def _dispatch(self, msg: dict) -> None:
        if msg.get("event") != "property-change":
            return
        obs_id = msg.get("id")
        data = msg.get("data")
        if obs_id == _OBS_VOLUME and data is not None and self.on_volume_change:
            try:
                self.on_volume_change(int(round(float(data))))
            except Exception as e:
                log.warning("on_volume_change failed: %s", e)
        elif obs_id == _OBS_PAUSE and self.on_pause_change:
            try:
                self.on_pause_change(bool(data))
            except Exception as e:
                log.warning("on_pause_change failed: %s", e)

    def _send(self, payload: dict) -> None:
        sock = self._sock
        if sock is None:
            return
        data = (json.dumps(payload) + "\n").encode()
        with self._send_lock:
            try:
                sock.sendall(data)
            except OSError as e:
                log.warning("mpv IPC send failed: %s", e)
                self._close_socket()
