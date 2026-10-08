"""Shared causal 15m features for historical evaluation and live paper signals."""
import numpy as np
import pandas as pd

from .strategy import Signal

CANDIDATES = ('donchian20', 'donchian55', 'ema_pullback', 'squeeze_breakout', 'ema_cross', 'rsi_rebound')
INTERVAL_MS = 900_000


def finite_ema(series, span):
    # Five spans, normalized: same finite history in batch research and runtime.
    length = span*5
    weights = (1-2/(span+1))**np.arange(length)
    weights /= weights.sum()
    values = np.full(len(series), np.nan)
    if len(series) >= length:
        values[length-1:] = np.convolve(series.to_numpy(float), weights, mode='valid')
    return pd.Series(values, index=series.index)


def features(frame):
    f = frame.copy()
    c, h, l, v = f[4], f[2], f[3], f[7]
    f['ema20'], f['ema50'] = finite_ema(c, 20), finite_ema(c, 50)
    f['sma200'] = c.rolling(200).mean()
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    f['atr'] = tr.rolling(14).mean()
    f['rvol'] = v/v.rolling(20).mean().shift(1)
    f['qvol24'] = v.rolling(96).sum()
    f['prev20'] = h.rolling(20).max().shift(1)
    f['prev55'] = h.rolling(55).max().shift(1)
    delta = c.diff()
    for length in (2, 14):
        gains = delta.clip(lower=0).rolling(length).mean()
        losses = (-delta.clip(upper=0)).rolling(length).mean()
        f[f'rsi{length}'] = 100*gains/(gains+losses)
    f['squeeze_recent'] = (4*c.rolling(20).std(ddof=0) < 3*tr.rolling(20).mean()).rolling(8).max().shift(1).fillna(0).astype(bool)
    f['strong_close'] = (c-l)/(h-l).replace(0,np.nan) >= 0.7
    f['trend'] = (c > f['sma200']) & (f['ema20'] > f['ema50']) & (c > f['ema20'])
    f['not_extended'] = ((c/f['ema20']-1) <= 0.04) & ((c/c.shift(4)-1) < 0.06)
    # Gaps poison the full indicator window; never fill missing market candles.
    consecutive = f[0].diff().eq(INTERVAL_MS).rolling(299).sum().eq(299)
    f['valid'] = consecutive & f['qvol24'].ge(5_000_000) & c.gt(0) & f['atr'].gt(0)
    f['stop_fraction'] = (2*f['atr']/c).clip(0.01,0.05)
    f['score'] = f['rvol'].clip(upper=10)*10+(c/c.shift(4)-1)*100
    return f


def masks(f):
    c = f[4]
    trend = f['valid'] & f['trend'] & f['not_extended'] & f['strong_close']
    return {
        'donchian20': trend & c.gt(f['prev20']) & f['rvol'].ge(2),
        'donchian55': trend & c.gt(f['prev55']) & f['rvol'].ge(1.5),
        'ema_pullback': trend & f[3].shift().le(f['ema20'].shift()) & f[4].shift().gt(f['ema50'].shift()) & c.gt(f[2].shift()) & f['rvol'].ge(1.25) & f['rsi14'].between(45,70),
        'squeeze_breakout': trend & f['squeeze_recent'] & c.gt(f['prev20']) & f['rvol'].ge(1.5),
        'ema_cross': trend & f['ema20'].shift().le(f['ema50'].shift()) & f['rvol'].ge(1),
        'rsi_rebound': f['valid'] & c.gt(f['sma200']) & f['rsi2'].shift().lt(10) & f['rsi2'].gt(20) & f['rvol'].ge(0.75),
    }


def from_feature(symbol, row, strategy):
    return Signal(symbol, int(row[0]), strategy, float(row[4]), float(row['stop_fraction']), float(row['score']),
                  f"{strategy} 15m | حجم ×{row['rvol']:.2f} | ATR {row['atr']/row[4]:.2%}")


def closed_features(rows, now_ms):
    f = pd.DataFrame(rows).apply(pd.to_numeric, errors='coerce')
    f = f[f[6] < now_ms].reset_index(drop=True)
    if len(f) < 300 or now_ms-int(f.iloc[-1,6]) > INTERVAL_MS+60_000 or f.isna().any().any():
        return None
    if not np.isfinite(f.to_numpy()).all() or (f[[1,2,3,4]] <= 0).any().any():
        return None
    return features(f)


def evaluate(symbol, rows, now_ms, strategy):
    if strategy not in CANDIDATES:
        return None
    f = closed_features(rows, now_ms)
    if f is None:
        return None
    if bool(masks(f)[strategy].iloc[-1]):
        return from_feature(symbol, f.iloc[-1], strategy)
    return None
