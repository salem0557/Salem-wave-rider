from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from research.backtest import MemoryStore, intrabar
from wave_rider.config import Config
from wave_rider.engine import Engine
from wave_rider.researched import closed_features, evaluate, features, masks
from wave_rider.research_runtime import Research
from wave_rider.store import Store
from wave_rider.strategy import Signal


def bars(n=320):
    rows=[]
    for i in range(n):
        c=100+i*.02
        rows.append([i*900000,c-.005,c+.01,c-.02,c,1000,(i+1)*900000-1,100000,100,500,50000,0])
    rows[-1][1:5]=[rows[-2][4], rows[-2][4]+.71, rows[-2][4]-.01, rows[-2][4]+.7]
    rows[-1][7]=350000
    return rows


def test_shared_features_are_causal_and_live_window_matches_batch():
    rows=bars(1000)
    full=features(pd.DataFrame(rows))
    live=closed_features(rows[-320:],1000*900000)
    for key in ['ema20','ema50','sma200','atr','rvol','prev55','stop_fraction','score']:
        assert live.iloc[-1][key] == pytest.approx(full.iloc[-1][key])
    modified=pd.DataFrame(rows)
    modified.loc[801:,[1,2,3,4,7]] *= 7
    changed=features(modified)
    pd.testing.assert_frame_equal(full.iloc[:801],changed.iloc[:801])
    for name in masks(full):
        pd.testing.assert_series_equal(masks(full)[name].iloc[:801],masks(changed)[name].iloc[:801])


def test_closed_candles_only_and_no_signals_across_gaps():
    rows=bars()
    assert evaluate('BTCUSDT',rows,320*900000,'donchian20') is not None
    assert evaluate('BTCUSDT',rows,319*900000+1000,'donchian20') is None
    assert evaluate('BTCUSDT',rows,322*900000,'donchian20') is None
    del rows[50]
    assert evaluate('BTCUSDT',rows,320*900000,'donchian20') is None


def test_ambiguous_bar_uses_initial_stop_before_profit_target():
    cfg=Config()
    store=MemoryStore()
    engine=Engine(cfg,store,1_800_000_000)
    signal=Signal('BTCUSDT',1,'test',100,.02,1,'test')
    now=1_800_000_000
    engine.enter(signal,{'bid':100.,'ask':100.,'ask_qty':1000,'ts':now},now)
    stop=engine.s['positions']['BTCUSDT']['stop']
    arrays={'BTCUSDT':np.array([[now*1000,100,106,97,104,1000,0,100000,0,0,0,0]],float)}
    intrabar(engine,arrays,0,now,0)
    assert not engine.s['positions']
    assert len(store.completed)==1
    assert store.completed[0]<0
    assert store.fills[-1]['price']==pytest.approx(stop*(1-cfg.slippage))


def test_shadow_accounts_preserve_main_account_and_survive_restart(tmp_path):
    cfg=replace(Config(),data_dir=str(tmp_path),research_enabled=True,candle_interval='15m',candle_limit=320)
    main=Engine(cfg,Store(str(tmp_path/'main.db')))
    research=Research(cfg,main)
    assert main.s['research_blocked']
    rows=bars()
    now=320*900
    price=rows[-1][4]
    quotes={'BTCUSDT':{'bid':price,'ask':price*1.0001,'ask_qty':1000,'ts':now}}
    assert research.analyze({'BTCUSDT':rows},quotes,now)==[]
    assert main.s['cash']==300 and not main.s['positions']
    assert any(e.s['positions'] for e in research.shadows.values())
    saved={name:e.s.copy() for name,e in research.shadows.items()}
    research.close()
    restored=Research(cfg,main)
    assert all(e.s==saved[name] for name,e in restored.shadows.items())
    main.pause(False)
    signal=Signal('BTCUSDT',1,'test',price,.02,1,'test')
    assert not main.enter(signal,quotes['BTCUSDT'],now)
    restored.close()
    main.store.db.close()


def test_primary_pause_blocks_shadow_entries(tmp_path):
    cfg=replace(Config(),data_dir=str(tmp_path),research_enabled=True)
    main=Engine(cfg,Store(str(tmp_path/'main.db')))
    research=Research(cfg,main)
    main.pause(True)
    rows=bars(); now=320*900; price=rows[-1][4]
    research.analyze({'BTCUSDT':rows},{'BTCUSDT':{'bid':price,'ask':price,'ask_qty':1000,'ts':now}},now)
    assert all(not e.s['positions'] for e in research.shadows.values())
    research.close()
    main.store.db.close()
