from __future__ import annotations

_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[int]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    rng = hi - lo or 1
    last = len(_BLOCKS) - 1
    return "".join(_BLOCKS[(v - lo) * last // rng] for v in values)
