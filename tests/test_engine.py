import math
import pytest

from wave_rider.config import Config
from wave_rider.engine import Engine
from wave_rider.store import Store
from wave_rider.strategy import Signal

NOW = 1_800_000_000.0


def quote(price=100, now=NOW, **kw):
    return dict(bid=price, ask=price*1.0002, ask_qty=1000, ts=now, **kw)


def signal(symbol='BTCUSDT', candle=1, price=100):
    return Signal(symbol, candle, 'volume_breakout', price, 0.02, 90, 'fixture')


@pytest.fixture
def engine(tmp_path):
    return Engine(Config(), Store(str(tmp_path/'ledger.db')), NOW)


def test_entry_size_costs_and_persistence(engine):
    assert engine.enter(signal(), quote(), NOW)
    p = engine.s['positions']['BTCUSDT']
    assert p['cost'] == pytest.approx(60)
    assert engine.s['cash'] == pytest.approx(240)
    assert engine.equity() < 300
    assert engine.s['fees'] > 0
    restored = Engine(engine.cfg, engine.store, NOW+1)
    assert restored.s == engine.s
    assert restored.s['started_at'] == NOW
    assert len(engine.store.pending()) == 1


def test_stop_gap_fills_at_bid_not_stop(engine):
    engine.enter(signal(), quote(), NOW)
    engine.monitor({'BTCUSDT': quote(90, NOW+5)}, NOW+5)
    sell = engine.store.events()[0]
    assert sell['price'] == pytest.approx(90*(1-engine.cfg.slippage))
    assert sell['pnl'] < -5
    assert not engine.s['positions']
    assert engine.s['cash'] == pytest.approx(300+engine.s['realized_pnl'])


def test_partial_then_trailing_conserves_ledger(engine):
    engine.enter(signal(), quote(), NOW)
    p = engine.s['positions']['BTCUSDT']
    qty = p['qty']
    engine.monitor({'BTCUSDT': quote(105, NOW+5)}, NOW+5)
    p = engine.s['positions']['BTCUSDT']
    assert p['qty'] == pytest.approx(qty/2)
    assert p['partial'] and p['stop'] > p['entry']
    old_stop = p['stop']
    engine.monitor({'BTCUSDT': quote(110, NOW+10)}, NOW+10)
    assert p['stop'] > old_stop
    engine.monitor({'BTCUSDT': quote(107, NOW+15)}, NOW+15)
    assert not engine.s['positions']
    assert engine.s['cash'] == pytest.approx(300+engine.s['realized_pnl'])
    assert engine.s['wins'] == 1
    assert engine.s['closed_trades'] == 1


@pytest.mark.parametrize('bad_quote', [None, quote(now=NOW-31), quote(now=NOW+1),
                                       {'bid': 100, 'ask': 101, 'ask_qty': 1000, 'ts': NOW},
                                       {'bid': float('nan'), 'ask': 100, 'ts': NOW}])
def test_rejects_unavailable_stale_future_or_wide_quotes(engine, bad_quote):
    assert not engine.enter(signal(), bad_quote, NOW)
    assert engine.s['cash'] == 300


def test_does_not_fill_exits_without_fresh_price(engine):
    engine.enter(signal(), quote(), NOW)
    engine.monitor({'BTCUSDT': quote(80, NOW)}, NOW+60)
    assert 'BTCUSDT' in engine.s['positions']
    assert len(engine.store.events()) == 1


def test_position_limit_pause_and_duplicate_cooldown(engine):
    for sym in ['AUSDT', 'BUSDT', 'CUSDT']:
        assert engine.enter(signal(sym), quote(), NOW)
    assert not engine.enter(signal('DUSDT'), quote(), NOW)
    engine.pause(True)
    engine.monitor({s: quote(90, NOW+5) for s in engine.s['positions']}, NOW+5)
    assert not engine.s['positions']  # Pause never prevents exits.
    engine.pause(False)
    assert not engine.enter(signal(), quote(now=NOW+6), NOW+6)  # Daily halt on loss.
    assert not engine.enter(signal('AUSDT', 2), quote(now=NOW+6), NOW+6)


def test_week_end_survives_restart_and_closes_only_with_fresh_quote(engine):
    engine.enter(signal(), quote(), NOW)
    end = NOW+7*86400+1
    engine.monitor({}, end)
    assert engine.s['terminal']
    assert engine.s['positions']
    restored = Engine(engine.cfg, engine.store, end)
    restored.monitor({'BTCUSDT': quote(100, end)}, end)
    assert not restored.s['positions']
    assert not restored.enter(signal('ETHUSDT'), quote(now=end), end)


def test_daily_halt_resets_next_utc_day_but_terminal_does_not(engine):
    engine.s['cash'] = 284
    engine.monitor({}, NOW)
    assert engine.s['daily_halt']
    engine.monitor({}, NOW+86400)
    assert not engine.s['daily_halt']
    engine.s['cash'] = 250
    engine.monitor({}, NOW+86401)
    assert engine.s['terminal']
    engine.monitor({}, NOW+2*86400)
    assert engine.s['terminal']


def test_risk_budget_low_liquidity_and_reentry(engine):
    tiny = quote()
    tiny['ask_qty'] = 0.01
    assert not engine.enter(signal(), tiny, NOW)
    assert engine.enter(signal(), quote(), NOW)
    p = engine.s['positions']['BTCUSDT']
    risk = p['cost']-p['qty']*p['stop']*(1-engine.cfg.slippage)*(1-engine.cfg.fee_rate)
    assert risk <= 300*engine.cfg.risk_per_trade+1e-9
    engine.exit('BTCUSDT', 100, NOW+1, 1, 'test close')
    assert not engine.enter(signal(candle=2), quote(now=NOW+2), NOW+2)
    assert not engine.enter(signal(candle=1), quote(now=NOW+4000), NOW+4000)
    assert engine.enter(signal(candle=2), quote(now=NOW+4000), NOW+4000)


def test_live_mode_is_impossible(monkeypatch):
    monkeypatch.setenv('TRADING_MODE', 'live')
    with pytest.raises(ValueError, match='Only paper'):
        Config.from_env()


def test_target_is_a_halt_not_a_promise(engine):
    engine.s['cash'] = 600
    assert not engine.enter(signal(), quote(), NOW)
    assert engine.s['terminal'] == 'بلوغ الهدف التجريبي'
