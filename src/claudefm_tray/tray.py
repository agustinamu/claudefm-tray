from __future__ import annotations

import logging
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import Gdk, GLib, Gtk

from .config import read_url, runtime_socket
from .stream import MpvController, StreamInfo, resolve_stream

log = logging.getLogger(__name__)

UPTIME_REFRESH_S = 30
VOLUME_POLL_S = 2
WATCHDOG_S = 30
VOLUME_STEP = 5
VOLUME_LEVELS = (0, 25, 50, 75, 100, 125)


class TrayApp:
    def __init__(self, start_paused: bool = False) -> None:
        self.url = read_url()
        self.info: StreamInfo | None = None
        self.mpv = MpvController(runtime_socket())
        self.volume: int = 100
        self.start_paused = start_paused
        self.volume_radio_items: dict[int, Gtk.RadioMenuItem] = {}

        self.indicator = AppIndicator.Indicator.new(
            "claudefm-tray",
            "multimedia-player",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("claudeFM")
        self.indicator.set_label("♪ …", "♪ 100%")
        self.indicator.connect("scroll-event", self._on_scroll)

        self._build_menu()

        threading.Thread(target=self._bootstrap, daemon=True).start()
        GLib.timeout_add_seconds(UPTIME_REFRESH_S, self._refresh_uptime)
        GLib.timeout_add_seconds(VOLUME_POLL_S, self._sync_volume)
        GLib.timeout_add_seconds(WATCHDOG_S, self._watchdog)

    def _build_menu(self) -> None:
        self.menu = Gtk.Menu()

        self.title_item = Gtk.MenuItem(label="cargando…")
        self.title_item.set_sensitive(False)
        self.menu.append(self.title_item)

        self.uptime_item = Gtk.MenuItem(label="")
        self.uptime_item.set_sensitive(False)
        self.menu.append(self.uptime_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        self.volume_item = Gtk.MenuItem(label="Volumen 100%")
        volume_submenu = Gtk.Menu()
        group: Gtk.RadioMenuItem | None = None
        for level in VOLUME_LEVELS:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, f"{level}%")
            if group is None:
                group = item
            item.connect("activate", self._on_volume_radio, level)
            volume_submenu.append(item)
            self.volume_radio_items[level] = item
        self.volume_item.set_submenu(volume_submenu)
        self.menu.append(self.volume_item)

        self.play_item = Gtk.MenuItem(label="Pausa")
        self.play_item.connect("activate", self._on_toggle_pause)
        self.menu.append(self.play_item)

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
        self.mpv.start(
            info.hls_url, info.title, volume=self.volume, paused=self.start_paused
        )
        GLib.idle_add(self._refresh_uptime)
        GLib.idle_add(self._refresh_label)
        GLib.idle_add(self._sync_pause_label)

    def _set_error(self, msg: str) -> bool:
        self.title_item.set_label(f"error: {msg[:60]}")
        self.indicator.set_label("✗", "✗")
        return False

    def _refresh_label(self) -> bool:
        self.indicator.set_label(f"♪ {self.volume}%", "♪ 100%")
        self.volume_item.set_label(f"Volumen {self.volume}%")
        if self.info:
            self.title_item.set_label(self.info.title[:80])
        self._sync_volume_radios()
        return False

    def _sync_volume_radios(self) -> None:
        # Light up the closest radio item without retriggering the signal.
        closest = min(VOLUME_LEVELS, key=lambda lvl: abs(lvl - self.volume))
        item = self.volume_radio_items.get(closest)
        if item and not item.get_active():
            item.handler_block_by_func(self._on_volume_radio)
            item.set_active(True)
            item.handler_unblock_by_func(self._on_volume_radio)

    def _refresh_uptime(self) -> bool:
        if not self.info or self.info.release_ts is None:
            return True
        secs = int(time.time()) - self.info.release_ts
        h, rem = divmod(secs, 3600)
        m = rem // 60
        self.uptime_item.set_label(f"on for {h}h {m:02d}m")
        return True

    def _sync_volume(self) -> bool:
        v = self.mpv.get_volume()
        if v is not None and v != self.volume:
            self.volume = v
            self._refresh_label()
        return True

    def _change_volume(self, delta: int) -> None:
        self.mpv.add_volume(delta)
        # Optimistic UI update; _sync_volume will reconcile if needed.
        self.volume = max(0, min(130, self.volume + delta))
        self._refresh_label()

    def _on_scroll(self, _indicator, _steps, direction) -> None:
        if direction == Gdk.ScrollDirection.UP:
            self._change_volume(+VOLUME_STEP)
        elif direction == Gdk.ScrollDirection.DOWN:
            self._change_volume(-VOLUME_STEP)

    def _on_volume_radio(self, item: Gtk.RadioMenuItem, level: int) -> None:
        if not item.get_active():
            return
        self.volume = level
        self.mpv.set_volume(level)
        self.indicator.set_label(f"♪ {self.volume}%", "♪ 100%")
        self.volume_item.set_label(f"Volumen {self.volume}%")

    def _on_toggle_pause(self, _w: Gtk.MenuItem) -> None:
        self.mpv.toggle_pause()
        self._sync_pause_label()

    def _sync_pause_label(self) -> bool:
        self.play_item.set_label("Reanudar" if self.mpv.is_paused() else "Pausa")
        return False

    def _watchdog(self) -> bool:
        # YouTube HLS URLs expire (~6h). If mpv died, resolve and restart.
        if self.info and not self.mpv.is_alive():
            log.info("mpv died — re-resolving stream")
            threading.Thread(target=self._bootstrap, daemon=True).start()
        return True

    def _on_quit(self, _w: Gtk.MenuItem) -> None:
        self.mpv.stop()
        Gtk.main_quit()


def run(start_paused: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    TrayApp(start_paused=start_paused)
    Gtk.main()
