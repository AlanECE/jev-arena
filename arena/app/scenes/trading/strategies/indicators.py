"""Indicateurs simples calcules sur les bougies fermees (aucune donnee future)."""
import math


def closes(bars):
    return [b["c"] for b in bars]


def ret_bps(bars, k: int) -> float:
    if len(bars) <= k or bars[-1 - k]["c"] == 0:
        return 0.0
    return (bars[-1]["c"] / bars[-1 - k]["c"] - 1) * 1e4


def last_returns_bps(bars, n: int = 10) -> list:
    out = []
    for i in range(max(1, len(bars) - n), len(bars)):
        p0 = bars[i - 1]["c"]
        out.append(round((bars[i]["c"] / p0 - 1) * 1e4, 2) if p0 else 0.0)
    return out


def ema(values, span: int):
    if not values:
        return None
    a = 2 / (span + 1)
    e = values[0]
    for v in values[1:]:
        e = a * v + (1 - a) * e
    return e


def rsi(bars, n: int = 14) -> float | None:
    c = closes(bars[-(n + 1):])
    if len(c) < n + 1:
        return None
    gains = sum(max(0, c[i] - c[i - 1]) for i in range(1, len(c)))
    losses = sum(max(0, c[i - 1] - c[i]) for i in range(1, len(c)))
    if gains + losses == 0:
        return 50.0
    return 100 * gains / (gains + losses)


def volatility_bps(bars, n: int = 60) -> float:
    r = last_returns_bps(bars, n)
    if len(r) < 2:
        return 0.0
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))


def volume_z(bars, n: int = 60) -> float:
    v = [b["v"] for b in bars[-n:]]
    if len(v) < 5:
        return 0.0
    m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) or 1e-9
    return (v[-1] - m) / sd


def buy_ratio(bars, n: int = 15) -> float | None:
    v = sum(b["v"] for b in bars[-n:])
    if v <= 0:
        return None
    return sum(b.get("vb", 0) for b in bars[-n:]) / v


def range_position(bars, n: int = 60) -> float | None:
    w = bars[-n:]
    if not w:
        return None
    hi = max(b["h"] for b in w)
    lo = min(b["l"] for b in w)
    return 0.5 if hi == lo else (bars[-1]["c"] - lo) / (hi - lo)
