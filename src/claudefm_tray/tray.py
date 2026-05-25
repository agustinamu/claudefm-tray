from __future__ import annotations

import logging
import threading
import time
from collections import deque

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import GLib, Gtk

from .config import read_url, runtime_socket
from .sparkline import sparkline
from .stream import MpvController, StreamInfo, poll_viewers, resolve_stream

log = logging.getLogger(__name__)

POLL_SECONDS = 30
UPTIME_REFRESH_SECONDS = 60
BUFFER_SIZE = 8


class TrayApp:
    def __init__(self) -> None:
        self.url = read_url()
        self.info: StreamInfo | None = None
        self.viewer_buf: deque[int] = deque(maxlen=BUFFER_SIZE)
        self.mpv = MpvController(runtime_socket())

        self.indicator = AppIndicator.Indicator.new(
            "claudefm-tray",
            "multimedia-player",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("claudeFM")
        self.indicator.set_label("♪ …", "♪ 0000 ▁▂▃▄▅▆▇█")

        self._build_menu()

        threading.Thread(target=self._bootstrap, daemon=True).start()
        GLib.timeout_add_seconds(UPTIME_REFRESH_SECONDS, self._refresh_label)
        GLib.timeout_add_seconds(POLL_SECONDS, self._schedule_poll)

    def _build_menu(self) -> None:
        self.menu = Gtk.Menu()

        self.title_item = Gtk.MenuItem(label="cargando…")
        self.title_item.set_sensitive(False)
        self.menu.append(self.title_item)

        self.uptime_item = Gtk.MenuItem(label="")
        self.uptime_item.set_sensitive(False)
        self.menu.append(self.uptime_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        self.play_item = Gtk.MenuItem(label="Pausa")
        self.play_item.connect("activate", self._on_toggle)
        self.menu.append(self.play_item)

        refresh_item = Gtk.MenuItem(label="Recargar viewers")
        refresh_item.connect("activate", lambda _w: self._schedule_poll())
        self.menu.append(refresh_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Salir")
        quit_item.connect("activate", self._on_quit)
        self.menu.append(quit_item)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

    def _bootstrap(self) -> None:
        try:
            info = resolve_stream(self.url)
        except Exception as e:
            log.error("resolve failed: %s", e)
            GLib.idle_add(self._set_error, str(e))
            return
        self.info = info
        if info.viewers is not None:
            self.viewer_buf.append(info.viewers)
        self.mpv.start(info.hls_url, info.title)
        GLib.idle_add(self._refresh_label)

    def _set_error(self, msg: str) -> bool:
        self.title_item.set_label(f"error: {msg[:60]}")
        self.indicator.set_label("✗", "✗")
        return False

    def _refresh_label(self) -> bool:
        if not self.info:
            return True
        viewers = self.viewer_buf[-1] if self.viewer_buf else None
        spark = sparkline(list(self.viewer_buf))
        viewers_str = str(viewers) if viewers is not None else "?"
        self.indicator.set_label(f"♪ {viewers_str} {spark}", "♪ 0000 ▁▂▃▄▅▆▇█")
        self.title_item.set_label(self.info.title[:80])
        self.uptime_item.set_label(self._uptime_text())
        return True

    def _uptime_text(self) -> str:
        if not self.info or self.info.release_ts is None:
            return ""
        secs = int(time.time()) - self.info.release_ts
        h, rem = divmod(secs, 3600)
        m = rem // 60
        return f"on for {h}h {m:02d}m"

    def _schedule_poll(self) -> bool:
        threading.Thread(target=self._poll_once, daemon=True).start()
        return True

    def _poll_once(self) -> None:
        v = poll_viewers(self.url)
        if v is not None:
            GLib.idle_add(self._on_viewers, v)

    def _on_viewers(self, v: int) -> bool:
        self.viewer_buf.append(v)
        self._refresh_label()
        return False

    def _on_toggle(self, _w: Gtk.MenuItem) -> None:
        self.mpv.toggle_pause()
        paused = self.mpv.is_paused()
        self.play_item.set_label("Reanudar" if paused else "Pausa")

    def _on_quit(self, _w: Gtk.MenuItem) -> None:
        self.mpv.stop()
        Gtk.main_quit()


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    TrayApp()
    Gtk.main()
