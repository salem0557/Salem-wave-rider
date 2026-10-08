"""Forward paper comparisons isolated from the user's existing $300 ledger."""
import json
from pathlib import Path

from .engine import Engine, valid_quote
from .researched import CANDIDATES, closed_features, from_feature, masks
from .store import Store


class ShadowStore(Store):
    def save(self, state, events=(), messages=()):
        super().save(state, events, ())
    def enqueue(self, message):
        pass  # Only the main account's Telegram carries the comparison summary.


class Research:
    def __init__(self, cfg, main):
        self.cfg, self.main = cfg, main
        self.policy = json.loads(Path(__file__).with_name('policy.json').read_text())
        approved = self.policy.get('approved_strategy')
        if approved is not None and (approved not in CANDIDATES or not all(self.policy['promotion_checks'].values())):
            raise ValueError('Invalid strategy promotion policy')
        self.approved = approved
        self.main.s['research_blocked'] = approved is None
        self.main.store.save(self.main.s)
        self.shadows = {name: Engine(cfg, ShadowStore(str(Path(cfg.data_dir)/f'shadow_{name}.db'))) for name in CANDIDATES}

    def announce(self):
        if self.main.store.meta('research_policy_version') != self.policy['version']:
            self.main.store.enqueue('🔬 نتائج بحث الاستراتيجيات\n'
                'لم تجتز أي من القواعد الست شروط الاعتماد بعد الرسوم والانزلاق.\n'
                'المحفظة الأساسية: لا دخول جديد، مع استمرار إدارة خروج المراكز القائمة.\n'
                'بدأت مقارنة أمامية منفصلة: 6 حسابات محاكاة، كل منها 300 USDT افتراضي. ليست أموالًا مضافة للمحفظة الأساسية.\n'
                'أرسل /strategies لنتائج المقارنة. لا يوجد تفعيل تلقائي لاستراتيجية غير معتمدة.')
            self.main.store.set_meta('research_policy_version',self.policy['version'])

    def analyze(self, rows_by_symbol, quotes, now):
        frames = {s:closed_features(rows,int(now*1000)) for s,rows in rows_by_symbol.items()}
        btc = frames.get('BTCUSDT')
        if btc is None:
            return []
        latest = btc.iloc[-1]
        regime = latest[4]>latest['sma200'] and latest[4]/btc.iloc[-5][4]-1 > -0.025
        for engine in self.shadows.values():
            engine.start(now)
        by_name = {name:[] for name in CANDIDATES}
        if regime:
            for symbol,frame in frames.items():
                if frame is None or frame.iloc[-1][0] != latest[0]:
                    continue
                for name,mask in masks(frame).items():
                    if bool(mask.iloc[-1]):
                        by_name[name].append(from_feature(symbol,frame.iloc[-1],name))
        for name,engine in self.shadows.items():
            if self.main.s['paused'] or not all(valid_quote(quotes.get(s),now,self.cfg.stale_seconds) for s in engine.s['positions']):
                continue
            for signal in sorted(by_name[name],key=lambda s:(-s.score,s.symbol)):
                engine.enter(signal,quotes.get(signal.symbol),now)
        return by_name[self.approved] if self.approved else []

    def monitor(self, quotes, now):
        for engine in self.shadows.values():
            engine.monitor(quotes,now)

    def report(self):
        lines = ['🔬 مقارنة الاستراتيجيات — حسابات ورقية منفصلة',
                 'المعتمد للمحفظة الأساسية: '+(self.approved or 'لا شيء؛ لا مداخل جديدة'),
                 f"اختبار الأقل خسارة خارج العينة: {self.policy['holdout_mean_week_return_pct']:+.2f}% متوسط أسبوعي.",
                 'المقارنة الأمامية الحالية (كل حساب بدأ بـ300):']
        for name,e in sorted(self.shadows.items(),key=lambda item:item[1].equity(),reverse=True):
            lines.append(f"{name}: {e.equity():.2f} USDT | {e.equity()-300:+.2f} | مكتملة {e.s['closed_trades']} | مفتوحة {len(e.s['positions'])}")
        lines.append('هذه مقارنة تجريبية، لا تثبت ربحية ولا تستخدم المحفظة الأساسية. /pause يمنع دخول الجميع؛ المخارج تستمر.')
        return '\n'.join(lines)

    def close(self):
        for engine in self.shadows.values():
            engine.store.db.close()
