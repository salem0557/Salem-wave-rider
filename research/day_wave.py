"""One fixed hybrid, daily trials; reused history is exploratory, never holdout."""
import hashlib
import json

from research.backtest import ROOT, prepare, run_candidate
from wave_rider.day_wave import NAME, config


def main():
    protocol = json.loads((ROOT/'research/protocol.json').read_text())
    arrays, signals, regime = prepare(protocol)
    artifact = {'strategy': NAME, 'historical_status': 'reused_exploratory_not_independent_holdout',
                'protocol_sha256': hashlib.sha256((ROOT/'research/day_wave_protocol.json').read_bytes()).hexdigest(),
                'primary_approved': False, 'results': {}}
    for year, start, end in [('2024','2024-01-01','2025-01-01'),('2025','2025-01-01','2026-01-01'),('2026','2026-01-01','2026-10-01')]:
        artifact['results'][year] = {}
        for label in ('base','stress'):
            raw = run_candidate(NAME, arrays, signals, regime, start, end, protocol[label+'_costs'],
                                period_days=1,config_override=config)
            # Shared replay defaults to weekly terminology; this run uses 1-day trials.
            result = {k.replace('weekly','daily').replace('weeks','days').replace('week','day'):v for k,v in raw.items()}
            active = [d for d in result['daily_results'] if d['trades']>0]
            result['no_trade_days'] = result['days']-len(active)
            result['active_days'] = len(active)
            result['mean_active_day_return_pct'] = sum(d['return_pct'] for d in active)/len(active) if active else None
            result['mean_end_equity'] = 300*(1+result['mean_day_return_pct']/100)
            artifact['results'][year][label] = result
            print(year,label,{k:v for k,v in result.items() if k!='daily_results'},flush=True)
    (ROOT/'reports/day_wave.json').write_text(json.dumps(artifact,indent=2,allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
