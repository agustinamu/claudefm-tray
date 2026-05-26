#!/usr/bin/env bash
# claudefm-tray — install script
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

# Required system packages, with the reason each one is here:
#   yt-dlp                              — resolves the YouTube HLS URL
#   mpv                                 — actually plays the audio
#   python3-gi                          — Python ⇄ GLib bindings
#   gir1.2-gtk-3.0                      — GTK3 typelibs (Gdk, Gtk)
#   gir1.2-ayatanaappindicator3-0.1     — tray icon library on GNOME
#   gnome-shell-extension-appindicator  — GNOME extension that paints the icon
REQUIRED=(
  yt-dlp mpv
  python3-gi gir1.2-gtk-3.0
  gir1.2-ayatanaappindicator3-0.1
  gnome-shell-extension-appindicator
)

# Optional but strongly recommended:
#   mpv-mpris — publishes mpv on the MPRIS bus so the keyboard's
#               multimedia keys (XF86AudioPlay etc.) pause/resume it.
OPTIONAL=(mpv-mpris)

echo "==> apt install (sudo)"
sudo apt install -y "${REQUIRED[@]}" "${OPTIONAL[@]}"

# uv needs the system Python so it can see gi/AyatanaAppIndicator3 via
# --system-site-packages. We pin to /usr/bin/python3 (Ubuntu 24.04 ships 3.12).
echo "==> uv venv (with system-site-packages for PyGObject)"
uv venv --python /usr/bin/python3 --system-site-packages --allow-existing

echo "==> uv sync"
uv sync --inexact

echo "==> autostart entry"
mkdir -p "$HOME/.config/autostart"
cp -v claudefm-tray.desktop "$HOME/.config/autostart/"

echo "==> application launcher (so it shows up in the Ubuntu app search)"
mkdir -p "$HOME/.local/share/applications"
cp -v claudefm-tray.desktop "$HOME/.local/share/applications/"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

cat <<EOF

done.

Run now:    .venv/bin/claudefm-tray
Or log out and back in — the tray will appear automatically.

The URL is read from ~/.config/claudefm/url (same as the original claudefm).
EOF
