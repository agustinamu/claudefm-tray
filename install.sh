#!/usr/bin/env bash
# claudefm-tray — install script
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

echo "==> system packages (sudo)"
sudo apt install -y \
  yt-dlp mpv \
  python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 \
  gnome-shell-extension-appindicator

# uv needs the system Python so it can see gi/AyatanaAppIndicator3 via
# --system-site-packages. We pin to /usr/bin/python3 (Ubuntu 24.04 ships 3.12).
echo "==> uv venv (with system-site-packages for PyGObject)"
uv venv --python /usr/bin/python3 --system-site-packages --allow-existing

echo "==> uv sync"
uv sync --inexact

echo "==> autostart entry"
mkdir -p "$HOME/.config/autostart"
cp -v claudefm-tray.desktop "$HOME/.config/autostart/"

cat <<EOF

done.

Run now:    .venv/bin/claudefm-tray
Or log out and back in — the tray will appear automatically.

The URL is read from ~/.config/claudefm/url (same as the original claudefm).
EOF
