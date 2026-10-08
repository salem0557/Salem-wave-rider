"""Fixed intraday adaptation: breakout -> first EMA pullback -> confirmation."""
from dataclasses import replace

import numpy as np
import pandas as pd

NAME = 'day_wave'


def config(base):
    return replace(base, risk_per_trade=.005, max_positions=2, daily_loss_limit=.02,
                   max_drawdown=.10, max_hold_seconds=86400, intraday_flatten=True)


def entry_mask(f):
    # Finite, simple-smoothed ADX rather than an infinite Wilder initialization.
    h, l, c = f[2], f[3], f[4]
    up, down = h.diff(), -l.diff()
    plus = up.where((up > down) & (up > 0), 0).rolling(14).sum()
    minus = down.where((down > up) & (down > 0), 0).rolling(14).sum()
    adx = (100*(plus-minus).abs()/(plus+minus).replace(0,np.nan)).rolling(14).mean()
    trend = f['trend'] & f['not_extended'] & f['strong_close']
    setup = trend & c.gt(f['prev55']) & f['rvol'].ge(2) & adx.ge(30)
    confirm = trend & c.gt(h.shift()) & f['rvol'].ge(1.25)
    # Setup warms from bar249; a max16-bar state is identical in a 320-bar live window.
    continuity = f[0].diff().eq(900_000).rolling(249).sum().eq(249)
    ready = continuity & f['qvol24'].ge(5_000_000) & f['atr'].gt(0)
    values = zip(ready, setup, confirm, c, l, f['ema20'], f['ema50'], f['prev55'], f['atr'])
    result = np.zeros(len(f),dtype=bool)
    active = None
    for i, (ok, breakout, resume, close, low, ema20, ema50, level, atr) in enumerate(values):
        if not ok:
            active = None
            continue
        if active is not None:
            start, floor, touched = active
            if i-start > 16 or close < floor or close < ema50 or (touched is not None and i-touched > 4):
                active = None
            elif touched is None:
                if low <= ema20:
                    active = (start, floor, i)
                continue
            else:
                if resume:
                    result[i] = True
                    active = None
                continue
        if breakout:
            active = (i, level-.5*atr, None)
    return pd.Series(result,index=f.index) & f['valid']
