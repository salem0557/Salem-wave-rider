from wave_rider.strategy import evaluate


def bars():
    rows = []
    for i in range(100):
        close = 100+i*0.01
        rows.append([i*60000, close-0.005, close+0.01, close-0.02, close, 1,
                     (i+1)*60000-1, 1000, 1, 1, 1, 0])
    rows[-1][1:5] = [100.98, 101.52, 100.97, 101.5]
    rows[-1][7] = 3500
    return rows


def test_volume_breakout_with_reason():
    s = evaluate('BTCUSDT', bars(), 100*60000)
    assert s and s.strategy == 'volume_breakout'
    assert s.candle_ms == 99*60000
    assert '3.50' in s.reason
    assert 0.01 <= s.stop_fraction <= 0.05


def test_no_lookahead_unfinished_candle():
    assert evaluate('BTCUSDT', bars(), 99*60000+10000) is None


def test_stale_gapped_low_volume_or_overextended_signal_rejected():
    assert evaluate('BTCUSDT', bars(), 102*60000) is None
    missing = bars()
    del missing[70]
    assert evaluate('BTCUSDT', missing, 100*60000) is None
    low = bars()
    low[-1][7] = 1100
    assert evaluate('BTCUSDT', low, 100*60000) is None
    extended = bars()
    extended[-1][2:5] = [121, 100, 120]
    assert evaluate('BTCUSDT', extended, 100*60000) is None
