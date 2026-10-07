"""Deterministic hypotheses, not predictions or guaranteed returns."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Signal:
    symbol: str
    candle_ms: int
    strategy: str
    price: float
    stop_fraction: float
    score: float
    reason: str


def ema(values, length):
    result = values[0]
    for value in values[1:]:
        result += (2 / (length + 1)) * (value - result)
    return result


def evaluate(symbol, rows, now_ms):
    # Never use an unfinished candle or a stale last close.
    bars = [r for r in rows if int(r[6]) < now_ms]
    if len(bars) < 60 or now_ms - int(bars[-1][6]) > 90_000:
        return None
    if any(int(b[0]) - int(a[0]) != 60_000 for a, b in zip(bars, bars[1:])):
        return None
    o = [float(r[1]) for r in bars]
    h = [float(r[2]) for r in bars]
    l = [float(r[3]) for r in bars]
    c = [float(r[4]) for r in bars]
    v = [float(r[7]) for r in bars]  # quote-asset volume, closed 1-minute bars
    if not all(math.isfinite(x) and x > 0 for x in o+h+l+c) or not all(math.isfinite(x) and x >= 0 for x in v):
        return None
    baseline = sum(v[-21:-1]) / 20
    if baseline <= 0:
        return None
    relative_volume = v[-1] / baseline
    atr = sum(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(len(c)-14, len(c))) / 14
    fast, slow = ema(c, 9), ema(c, 21)
    move = c[-1] / c[-6] - 1
    extension = c[-1] / slow - 1
    trend = c[-1] > fast > slow and c[-1] > o[-1]
    # Reject overextended candles and wick-dominated breakouts.
    strong_close = (c[-1]-l[-1]) / max(h[-1]-l[-1], 1e-12) >= 0.7
    if not trend or not strong_close or not 0.002 <= move <= 0.06 or extension > 0.04:
        return None
    breakout = c[-1] > max(h[-21:-1]) and relative_volume >= 2.0
    pullback = l[-2] <= ema(c[:-1], 9) and c[-2] >= ema(c[:-1], 21) and c[-1] > h[-2] and relative_volume >= 1.5
    if not (breakout or pullback):
        return None
    stop = max(0.01, min(0.05, 2 * atr / c[-1]))
    strategy = "volume_breakout" if breakout else "trend_pullback"
    reason = f"{strategy} | حجم ×{relative_volume:.2f} | زخم 5د {move:+.2%} | ATR {atr/c[-1]:.2%}"
    return Signal(symbol, int(bars[-1][0]), strategy, c[-1], stop,
                  min(relative_volume, 10) * 10 + move * 100, reason)
