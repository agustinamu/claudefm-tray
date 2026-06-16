from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)


# observe_property IDs — kept stable so the reader can route events by id.
_OBS_VOLUME = 1
_OBS_PAUSE = 2
_OBS_TITLE = 3
_OBS_PLAYLIST_POS = 4
_OBS_PLAYLIST_COUNT = 5


class MpvController:
    """Drives mpv over its JSON IPC socket.

    mpv resolves the URL itself through its yt-dlp hook, so a single video, a
    playlist or a live stream all work — we just hand it the original URL.
    Keeps one long-lived socket and a reader thread that dispatches
    property-change events to user-supplied callbacks. There is no polling;
    mpv pushes updates whenever a property changes (including the initial
    value, emitted automatically when observe_property is registered).
    """

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._playlist_pos = -1
        self._playlist_count = 0
        # Callbacks invoked from the reader thread — wrap with GLib.idle_add
        # on the consumer side if you need to touch GTK.
        self.on_volume_change: Callable[[int], None] | None = None
        self.on_pause_change: Callable[[bool], None] | None = None
        self.on_title_change: Callable[[str], None] | None = None
        self.on_playlist_change: Callable[[int, int], None] | None = None

    def start(
        self,
        url: str,
        volume: int = 100,
        paused: bool = False,
    ) -> bool:
        """Launch mpv on `url`. Returns True if the IPC socket came up.

        Returns False if mpv exited before the socket appeared (e.g. an
        invalid or private URL that yt-dlp could not resolve).
        """
        if self.is_alive():
            return True
        self._close_socket()
        self._playlist_pos = -1
        self._playlist_count = 0
        self.socket_path.unlink(missing_ok=True)
        cmd = [
            "mpv",
            "--no-video",
            f"--volume={volume}",
            "--no-input-terminal",
            "--really-quiet",
            "--loop-playlist=inf",
            "--ytdl=yes",
            "--ytdl-format=bestaudio/best",
            "--script-opts=ytdl_hook-ytdl_path=yt-dlp",
            f"--input-ipc-server={self.socket_path}",
        ]
        if paused:
            cmd.append("--pause")
        cmd.append(url)
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
            if self.proc.poll() is not None:
                log.warning("mpv exited before IPC socket appeared")
                return False
            time.sleep(0.1)
        else:
            log.warning("mpv IPC socket did not appear in 5s")
            return False
        if not self._open_socket():
            return False
        self._start_reader()
        # Subscribe to property changes. mpv emits the current value once at
        # registration, so we don't need a follow-up get_property.
        self._send({"command": ["observe_property", _OBS_VOLUME, "volume"]})
        self._send({"command": ["observe_property", _OBS_PAUSE, "pause"]})
        self._send({"command": ["observe_property", _OBS_TITLE, "media-title"]})
        self._send({"command": ["observe_property", _OBS_PLAYLIST_POS, "playlist-pos"]})
        self._send({"command": ["observe_property", _OBS_PLAYLIST_COUNT, "playlist-count"]})
        return True

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
        elif obs_id == _OBS_TITLE and data and self.on_title_change:
            try:
                self.on_title_change(str(data))
            except Exception as e:
                log.warning("on_title_change failed: %s", e)
        elif obs_id == _OBS_PLAYLIST_POS and isinstance(data, int):
            self._playlist_pos = data
            self._emit_playlist()
        elif obs_id == _OBS_PLAYLIST_COUNT and isinstance(data, int):
            self._playlist_count = data
            self._emit_playlist()

    def _emit_playlist(self) -> None:
        if not self.on_playlist_change:
            return
        try:
            self.on_playlist_change(self._playlist_pos, self._playlist_count)
        except Exception as e:
            log.warning("on_playlist_change failed: %s", e)

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
