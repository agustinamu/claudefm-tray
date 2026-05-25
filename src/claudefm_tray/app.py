from __future__ import annotations

import argparse

from .tray import run


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
    run(start_paused=args.paused)


if __name__ == "__main__":
    main()
