"""Causal, next-open, adverse-OHLC weekly portfolio replay using the paper engine."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from wave_rider.config import Config
from wave_rider.engine import Engine
from wave_rider.researched import features, masks, from_feature, CANDIDATES

ROOT = Path(__file__).resolve().parents[1]


class MemoryStore:
    def __init__(self):
        self.completed = []
        self.running = {}
        self.fills = []
    def load(self):
        return None
    def save(self, state, events=(), messages=()):
        for event in events:
            self.fills.append(event)
            if event['kind'] == 'sell':
                symbol = event['symbol']
                self.running[symbol] = self.running.get(symbol,0)+event['pnl']
                if symbol not in state['positions']:
                    self.completed.append(self.running.pop(symbol))
    def enqueue(self, message):
        pass


def quote(mid, ts, spread, ask_qty=1e12):
    return {'bid': float(mid)*(1-spread/2), 'ask': float(mid)*(1+spread/2), 'ask_qty': float(ask_qty), 'ts': float(ts)}


def prepare(protocol):
    frames, arrays, signals = {}, {}, {}
    for symbol in protocol['symbols']:
        frame = pd.read_pickle(ROOT/'data/research'/f'{symbol}.pkl')
        f = features(frame)
        frames[symbol] = f
        arrays[symbol] = frame.to_numpy(float)
        signals[symbol] = {}
        for name, mask in masks(f).items():
            signals[symbol][name] = {int(i): from_feature(symbol,f.iloc[i],name) for i in np.flatnonzero(mask.to_numpy())}
    times = arrays['BTCUSDT'][:,0]
    if any(not np.array_equal(a[:,0], times) for a in arrays.values()):
        raise ValueError('Replay requires aligned, complete universe; missing bars must not be filled')
    btc = frames['BTCUSDT']
    regime = (btc[4].gt(btc['sma200']) & (btc[4]/btc[4].shift(4)-1).gt(-0.025)).to_numpy()
    return arrays, signals, regime


def intrabar(engine, arrays, i, now, spread):
    """Adverse ambiguity: initial stop before target; raised stops may hit same bar.
    OHLC does not reveal order. No perfect intrabar execution is claimed.
    Stops fill at threshold less costs, except gaps already filled at open.
    """
    for symbol in sorted(list(engine.s['positions'])):
        if symbol not in engine.s['positions']:
            continue
        row = arrays[symbol][i]
        low, high = row[3]*(1-spread/2), row[2]*(1-spread/2)
        p = engine.s['positions'][symbol]
        def event(bid, offset):
            engine.monitor({symbol: {'bid': float(bid), 'ask': float(bid)*(1+spread), 'ts': now+offset}}, now+offset)
        if low <= p['stop']:
            event(p['stop'], 1)
            continue
        if not p['partial'] and high >= p['entry']+2*p['risk_distance']:
            event(p['entry']+2*p['risk_distance'], 2)
        if symbol in engine.s['positions']:
            event(high, 3)
        if symbol in engine.s['positions'] and low <= engine.s['positions'][symbol]['stop']:
            event(engine.s['positions'][symbol]['stop'], 4)


def run_candidate(name, arrays, signals, regime, start, end, costs):
    times = arrays['BTCUSDT'][:,0]/1000
    start_ts, end_ts = pd.Timestamp(start,tz='UTC').timestamp(), pd.Timestamp(end,tz='UTC').timestamp()
    cfg = replace(Config(), fee_rate=costs['fee_per_side'], slippage=costs['slippage_per_side'])
    spread = costs['full_spread']
    weeks, pnls, total_fees, benchmark = [], [], 0.0, []
    for left in np.arange(start_ts,end_ts,7*86400):
        right = left+7*86400
        if right > end_ts:
            break  # Exclude partial weeks from comparability.
        indices = np.flatnonzero((times>=left)&(times<right))
        if len(indices) != 7*96:
            raise ValueError('Incomplete week')
        store = MemoryStore()
        engine = Engine(cfg,store,left)
        engine.start(left)
        drawdown = 0
        for i in indices:
            now = float(times[i])
            opens = {s: quote(a[i,1],now,spread,a[i-1,5]*0.1) for s,a in arrays.items()}
            engine.monitor(opens,now)
            if i > 0 and regime[i-1] and not engine.s['terminal']:
                candidates = [by_name[name][i-1] for by_name in signals.values() if i-1 in by_name[name]]
                for signal in sorted(candidates,key=lambda s:(-s.score,s.symbol)):
                    engine.enter(signal,opens[signal.symbol],now)
            intrabar(engine,arrays,i,now,spread)
            closes = {s:quote(a[i,4],now+899,spread) for s,a in arrays.items()}
            engine.monitor(closes,now+899)
            drawdown = max(drawdown,1-engine.equity()/engine.s['peak_equity'])
        for symbol in list(engine.s['positions']):
            engine.exit(symbol,closes[symbol]['bid'],now+899,1,'weekly_test_settlement')
        pnl = engine.s['cash']-cfg.initial_cash
        weeks.append({'start':pd.Timestamp(left,unit='s',tz='UTC').isoformat(), 'pnl':pnl,
                      'end_equity':engine.s['cash'], 'return_pct':pnl/3,
                      'max_drawdown_pct':drawdown*100,'trades':len(store.completed),'fees':engine.s['fees']})
        pnls.extend(store.completed)
        total_fees += engine.s['fees']
        ratios = [a[indices[-1],4]/a[indices[0],1] for a in arrays.values()]
        # Equal-weight fully invested weekly benchmark, same round-trip modeled costs.
        friction = ((1-spread/2)*(1-cfg.slippage)*(1-cfg.fee_rate))/((1+spread/2)*(1+cfg.slippage)*(1+cfg.fee_rate))
        benchmark.append((float(np.mean(ratios))*friction-1)*100)
    gains = sum(p for p in pnls if p>0)
    losses = -sum(p for p in pnls if p<0)
    returns = np.array([w['return_pct'] for w in weeks])
    rng = np.random.default_rng(42)
    # Descriptive bootstrap, not multiple-testing-adjusted evidence of an edge.
    means = rng.choice(returns, size=(2000,len(returns)),replace=True).mean(axis=1)
    return {'strategy':name, 'weeks':len(weeks),'closed_trades':len(pnls),
            'sum_independent_week_pnl':float(sum(w['pnl'] for w in weeks)),
            'mean_week_return_pct':float(returns.mean()),'median_week_return_pct':float(np.median(returns)),
            'positive_weeks_fraction':float((returns>0).mean()),'worst_week_pct':float(returns.min()),
            'best_week_pct':float(returns.max()),'max_week_drawdown_pct':max(w['max_drawdown_pct'] for w in weeks),
            'profit_factor':gains/losses if losses else None,'win_rate':sum(p>0 for p in pnls)/len(pnls) if pnls else 0,
            'fees':total_fees,'weekly_mean_bootstrap_95_pct':np.quantile(means,[.025,.975]).tolist(),
            'benchmark_mean_week_return_pct':float(np.mean(benchmark)),
            'weeks_doubling_capital':int((returns>=100).sum()),'weekly_results':weeks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage',choices=['selection','holdout'],required=True)
    args = parser.parse_args()
    protocol = json.loads((ROOT/'research/protocol.json').read_text())
    arrays,signals,regime = prepare(protocol)
    if args.stage == 'selection':
        results = {}
        for name in CANDIDATES:
            results[name] = {}
            for phase,start,end in [('development',protocol['start'],protocol['development_end']),
                                    ('selection',protocol['development_end'],protocol['selection_end'])]:
                r = run_candidate(name,arrays,signals,regime,start,end,protocol['base_costs'])
                results[name][phase] = r
                print(name,phase,{k:v for k,v in r.items() if k!='weekly_results'},flush=True)
        ranked = sorted(CANDIDATES,key=lambda n:(results[n]['selection']['median_week_return_pct'],
                                                results[n]['selection']['profit_factor'] or 0),reverse=True)
        chosen = ranked[0]
        artifact = {'protocol_sha256':hashlib.sha256((ROOT/'research/protocol.json').read_bytes()).hexdigest(),
                    'selected_candidate':chosen,'ranking':ranked,'results':results}
        (ROOT/'reports/selection.json').write_text(json.dumps(artifact,indent=2,allow_nan=False)+'\n')
        print('FROZEN CANDIDATE FOR HOLDOUT:',chosen,flush=True)
    else:
        selection = json.loads((ROOT/'reports/selection.json').read_text())
        if selection['protocol_sha256'] != hashlib.sha256((ROOT/'research/protocol.json').read_bytes()).hexdigest():
            raise ValueError('Protocol changed after selection')
        name = selection['selected_candidate']
        results = {}
        for label,costs in [('base',protocol['base_costs']),('stress',protocol['stress_costs'])]:
            results[label] = run_candidate(name,arrays,signals,regime,protocol['selection_end'],protocol['end_exclusive'],costs)
            print('HOLDOUT',label,{k:v for k,v in results[label].items() if k!='weekly_results'},flush=True)
        b,s = results['base'],results['stress']
        selection_positive = selection['results'][name]['selection']['sum_independent_week_pnl']>0
        gate = {'selection_net_positive':selection_positive,'holdout_net_positive':b['sum_independent_week_pnl']>0,
                'holdout_profit_factor':(b['profit_factor'] or 0)>=1.1,'holdout_trades':b['closed_trades']>=60,
                'positive_weeks':b['positive_weeks_fraction']>=.5,'stress_net_positive':s['sum_independent_week_pnl']>0}
        artifact = {'selected_candidate':name,'promotion_checks':gate,'approved':all(gate.values()),'results':results}
        (ROOT/'reports/holdout.json').write_text(json.dumps(artifact,indent=2,allow_nan=False)+'\n')
        print('PROMOTION:',gate,all(gate.values()),flush=True)


if __name__ == '__main__':
    main()
