from dataclasses import replace

import pandas as pd
import pytest

from research.backtest import MemoryStore
from wave_rider.config import Config
from wave_rider.day_wave import config, entry_mask
from wave_rider.engine import Engine
from wave_rider.researched import features
from wave_rider.research_runtime import Research
from wave_rider.store import Store
from wave_rider.strategy import Signal
from tests.test_research import bars


def setup_frame():
    f = features(pd.DataFrame(bars(400)))
    f['prev55'] = f[4]+1  # Only one deliberate breakout.
    f['ema20'] = f[4]-1
    f['ema50'] = f[4]-2
    f['trend'] = f['not_extended'] = f['strong_close'] = True
    f['rvol'] = .5
    f.loc[300, 'prev55'] = f.loc[300,4]-.1
    f.loc[300, 'rvol'] = 2.5
    f.loc[301, 'ema20'] = f.loc[301,4]  # First touch.
    f.loc[303:306, 'rvol'] = 1.5
    return f


@pytest.mark.parametrize('interval_ms', [300000, 900000])
def test_breakout_needs_first_touch_then_confirmation_and_emits_once(interval_ms):
    f=setup_frame()
    f[0]=f[0]//900000*interval_ms
    f['interval_ms']=interval_ms
    assert entry_mask(f)[lambda x:x].index.tolist()==[303]
    assert entry_mask(f.iloc[20:])[lambda x:x].index.tolist()==[303]
    # An expired first touch cannot be replaced with a later, convenient pullback.
    f.loc[303:306,'rvol']=.5
    f.loc[309,'ema20']=f.loc[309,4]
    f.loc[310,'rvol']=2
    assert not entry_mask(f).any()


def test_breakout_failure_or_missing_bar_cancels_setup():
    for broken in ('floor','gap'):
        f=setup_frame()
        if broken=='floor':
            f.loc[302,4]=f.loc[300,4]-5
        else:
            f.loc[302,0]+=1
        assert not entry_mask(f).any()


def entered(now):
    engine=Engine(config(Config()),MemoryStore(),now)
    signal=Signal('BTCUSDT',int(now*1000),'day_wave',100,.02,1,'day test')
    q={'bid':100.,'ask':100.,'ask_qty':1000,'ts':now}
    assert engine.enter(signal,q,now)
    return engine,signal


def test_intraday_settlement_precedes_partial_profit_and_late_entry_blocked():
    day=1_800_000_000//86400*86400
    engine,signal=entered(day+12*3600)
    late=day+23*3600
    q={'bid':100.,'ask':100.,'ask_qty':1000,'ts':late}
    assert not engine.enter(replace(signal,symbol='ETHUSDT'),q,late)
    close=day+23*3600+45*60
    engine.monitor({'BTCUSDT':{'bid':106.,'ask':106.,'ts':close}},close)
    assert not engine.s['positions']
    assert engine.s['closed_trades']==1
    assert engine.store.fills[-1]['reason']=='إغلاق جلسة موجة اليوم UTC'


def test_outage_defers_exit_and_prevents_new_entries_until_old_day_flat():
    day=1_800_000_000//86400*86400
    engine,signal=entered(day+12*3600)
    now=day+86400+60
    engine.monitor({'BTCUSDT':{'bid':100.,'ask':100.,'ts':day+86300}},now)
    assert engine.s['positions']
    q={'bid':100.,'ask':100.,'ask_qty':1000,'ts':now}
    assert not engine.enter(replace(signal,symbol='ETHUSDT'),q,now)
    engine.monitor({'BTCUSDT':q},now)
    assert not engine.s['positions']
    assert engine.enter(replace(signal,symbol='ETHUSDT'),q,now)


def test_day_wave_uses_isolated_config_and_durable_labeled_telegram(tmp_path):
    cfg=replace(Config(),data_dir=str(tmp_path))
    main=Engine(cfg,Store(str(tmp_path/'main.db')))
    research=Research(cfg,main)
    day=research.shadows['day_wave']
    assert day.cfg.max_hold_seconds==86400 and day.cfg.risk_per_trade==.005
    assert research.shadows['ema_cross'].cfg.max_hold_seconds==21600
    day.store.enqueue('durable test')
    research.close()
    restored=Research(cfg,main)
    restored.relay_day_messages()
    messages=main.store.pending()
    assert len(messages)==1 and 'موجة اليوم' in messages[0]['message']
    assert main.s['cash']==300 and main.s['research_blocked']
    restored.relay_day_messages()
    assert len(main.store.pending())==1
    restored.close()
    main.store.db.close()
