# claudefm-tray

Indicador en la barra superior de GNOME para [claudeFM](https://github.com/sanhuaaan/claudefm) — escuchar la radio sin ocupar una terminal.

Muestra los viewers en directo y una mini sparkline al lado del icono. El menú expone el título del stream, el tiempo "on for X", un play/pausa y un refresh manual de viewers.

La URL del stream se lee de `~/.config/claudefm/url` (el mismo archivo que usa el `claudefm` original — basta con compartirlo o enlazarlo).

## ¿Reutiliza claudefm?

**No.** Es una reimplementación independiente en Python. No invoca el binario `claudefm` ni comparte código. Lo único que comparte es el formato del archivo de configuración (la URL).

Internamente:
- llama a `yt-dlp` para resolver el HLS y los viewers,
- arranca `mpv` con IPC propio,
- pinta la sparkline.

Si quieres en cambio un *wrapper* sobre el `claudefm` original con servicio systemd y soporte MPRIS para los controles multimedia, ese es el hermano [`claudefm-mpris`](../claudefm-mpris).

## Instalación

```bash
./install.sh
```

Lo que hace el script:

1. `sudo apt install` de: `yt-dlp`, `mpv`, `python3-gi`, `gir1.2-ayatanaappindicator3-0.1`, `gnome-shell-extension-appindicator` (necesaria para que el icono aparezca en GNOME moderno).
2. `uv venv --system-site-packages` para que el entorno virtual herede PyGObject del sistema (instalar PyGObject desde PyPI requeriría toolchain de C y los GIR typelibs — no compensa).
3. `uv sync` para instalar el paquete en modo editable.
4. Copia `claudefm-tray.desktop` a `~/.config/autostart/` para que arranque solo en cada sesión.

## Ejecutar

```bash
uv run claudefm-tray
# o tras instalar:
.venv/bin/claudefm-tray
```

Tras reiniciar sesión arranca automáticamente desde el autostart.

## Parar

Clic derecho en el icono → **Salir**. O directamente `pkill claudefm-tray`.

## Créditos

La idea, el formato del archivo de URL y la sparkline de viewers vienen de [sanhuaaan/claudefm](https://github.com/sanhuaaan/claudefm). Este proyecto es una reimplementación independiente como aplicación GTK de bandeja — sin fork, sin código compartido.
