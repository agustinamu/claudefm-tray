from __future__ import annotations

import logging
import signal
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import Gdk, GLib, Gtk

from .config import read_url, runtime_socket, write_url
from .stream import MpvController

log = logging.getLogger(__name__)

UPTIME_REFRESH_S = 30
WATCHDOG_S = 30
VOLUME_STEP = 5
VOLUME_LEVELS = (0, 25, 50, 75, 100, 125)


class TrayApp:
    def __init__(self, start_paused: bool = False) -> None:
        self.url = read_url()
        self.title: str = ""
        self.playlist_pos: int = -1
        self.playlist_count: int = 0
        self.playback_start: float | None = None
        self.mpv = MpvController(runtime_socket())
        self.mpv.on_volume_change = self._on_mpv_volume
        self.mpv.on_pause_change = self._on_mpv_pause
        self.mpv.on_title_change = self._on_mpv_title
        self.mpv.on_playlist_change = self._on_mpv_playlist
        self.volume: int = 100
        self.paused: bool = start_paused
        self.start_paused = start_paused
        self.volume_radio_items: dict[int, Gtk.RadioMenuItem] = {}
        self._bootstrap_lock = threading.Lock()
        self._bootstrapping = False

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

        self._spawn_bootstrap()
        GLib.timeout_add_seconds(UPTIME_REFRESH_S, self._refresh_uptime)
        GLib.timeout_add_seconds(WATCHDOG_S, self._watchdog)

        # Catch SIGTERM/SIGINT so we tear the indicator down cleanly —
        # otherwise the shell keeps a phantom icon until it notices the
        # DBus name dropped.
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, self._on_signal)
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, self._on_signal)

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

        self.url_item = Gtk.MenuItem(label="Cambiar URL…")
        self.url_item.connect("activate", self._on_change_url)
        self.menu.append(self.url_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Salir")
        quit_item.connect("activate", self._on_quit)
        self.menu.append(quit_item)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

    def _spawn_bootstrap(self) -> None:
        # Coalesce concurrent bootstrap requests: yt-dlp can take 10s+ and the
        # watchdog fires every 30s, so without this a slow resolve would
        # stack threads competing for the same IPC socket.
        with self._bootstrap_lock:
            if self._bootstrapping:
                return
            self._bootstrapping = True
        threading.Thread(target=self._bootstrap, daemon=True).start()

    def _bootstrap(self) -> None:
        try:
            ok = self.mpv.start(
                self.url, volume=self.volume, paused=self.start_paused
            )
        except Exception as e:
            log.error("mpv start failed: %s", e)
            GLib.idle_add(self._set_error, str(e))
            return
        finally:
            with self._bootstrap_lock:
                self._bootstrapping = False
        if not ok:
            GLib.idle_add(self._set_error, "no se pudo reproducir (¿URL válida?)")
            return
        self.playback_start = time.time()
        GLib.idle_add(self._refresh_uptime_once)
        GLib.idle_add(self._refresh_label)

    def _refresh_uptime_once(self) -> bool:
        self._refresh_uptime()
        return False

    def _set_error(self, msg: str) -> bool:
        self.playback_start = None
        self.title_item.set_label(f"error: {msg[:60]}")
        self.uptime_item.set_label("")
        self.indicator.set_label("✗", "✗")
        return False

    def _refresh_label(self) -> bool:
        self.indicator.set_label(f"♪ {self.volume}%", "♪ 100%")
        self.volume_item.set_label(f"Volumen {self.volume}%")
        if self.title:
            self.title_item.set_label(self.title[:80])
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
        if self.playback_start is None:
            self.uptime_item.set_label("")
            return True
        secs = int(time.time() - self.playback_start)
        h, rem = divmod(secs, 3600)
        m = rem // 60
        parts = []
        if self.playlist_count > 1 and self.playlist_pos >= 0:
            parts.append(f"pista {self.playlist_pos + 1}/{self.playlist_count}")
        parts.append(f"sonando {h}h {m:02d}m")
        self.uptime_item.set_label(" · ".join(parts))
        return True

    def _on_mpv_volume(self, v: int) -> None:
        # Called from the mpv reader thread — hop to the GTK main loop.
        GLib.idle_add(self._apply_volume, v)

    def _on_mpv_pause(self, paused: bool) -> None:
        GLib.idle_add(self._apply_pause, paused)

    def _on_mpv_title(self, title: str) -> None:
        GLib.idle_add(self._apply_title, title)

    def _on_mpv_playlist(self, pos: int, count: int) -> None:
        GLib.idle_add(self._apply_playlist, pos, count)

    def _apply_title(self, title: str) -> bool:
        self.title = title
        self.title_item.set_label(title[:80])
        return False

    def _apply_playlist(self, pos: int, count: int) -> bool:
        self.playlist_pos = pos
        self.playlist_count = count
        self._refresh_uptime()
        return False

    def _apply_volume(self, v: int) -> bool:
        if v != self.volume:
            self.volume = v
            self._refresh_label()
        return False

    def _apply_pause(self, paused: bool) -> bool:
        self.paused = paused
        self.play_item.set_label("Reanudar" if paused else "Pausa")
        return False

    def _change_volume(self, delta: int) -> None:
        self.mpv.add_volume(delta)
        # Optimistic UI update; the property-change event will reconcile.
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
        # The new pause state arrives via _on_mpv_pause; no sync IPC needed.

    def _on_change_url(self, _w: Gtk.MenuItem) -> None:
        dialog = Gtk.Dialog(title="Cambiar URL de claudeFM")
        dialog.set_modal(True)
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        ok = dialog.add_button("Aceptar", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dialog.set_default_response(Gtk.ResponseType.OK)

        box = dialog.get_content_area()
        box.set_spacing(6)
        box.set_border_width(12)
        label = Gtk.Label(label="URL del directo de YouTube:")
        label.set_xalign(0)
        box.add(label)
        entry = Gtk.Entry()
        entry.set_text(self.url)
        entry.set_width_chars(48)
        entry.set_activates_default(True)
        box.add(entry)
        dialog.show_all()

        response = dialog.run()
        new_url = entry.get_text().strip()
        dialog.destroy()

        if response == Gtk.ResponseType.OK and new_url and new_url != self.url:
            write_url(new_url)
            self.url = new_url
            log.info("URL changed — reloading stream")
            self._reload_stream()

    def _reload_stream(self) -> None:
        # Tear down the current stream and resolve the new URL from scratch.
        self.mpv.stop()
        self.title = ""
        self.playlist_pos = -1
        self.playlist_count = 0
        self.playback_start = None
        self.start_paused = False
        self.paused = False
        self.play_item.set_label("Pausa")
        self.title_item.set_label("cargando…")
        self.uptime_item.set_label("")
        self.indicator.set_label("♪ …", "♪ 100%")
        self._spawn_bootstrap()

    def _watchdog(self) -> bool:
        # Two failure modes to tell apart:
        #  - mpv dies seconds after start → bad/dead URL; don't spin re-trying.
        #  - mpv dies after running fine → live HLS expired (~6h); re-resolve.
        if self.playback_start is None or self.mpv.is_alive():
            return True
        ran = time.time() - self.playback_start
        if ran < 60:
            log.warning("mpv died %ds after start — giving up (¿URL válida?)", int(ran))
            GLib.idle_add(self._set_error, "el stream no se pudo mantener")
        else:
            log.info("mpv died after %ds — re-resolving", int(ran))
            self._spawn_bootstrap()
        return True

    def _on_quit(self, _w: Gtk.MenuItem) -> None:
        self._shutdown()

    def _on_signal(self) -> bool:
        log.info("signal received — shutting down")
        self._shutdown()
        return False

    def _shutdown(self) -> None:
        try:
            self.indicator.set_status(AppIndicator.IndicatorStatus.PASSIVE)
        except Exception as e:
            log.warning("indicator passive failed: %s", e)
        self.mpv.stop()
        Gtk.main_quit()


def run(start_paused: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    TrayApp(start_paused=start_paused)
    Gtk.main()
