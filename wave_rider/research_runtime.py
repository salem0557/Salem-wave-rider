"""Forward paper comparisons isolated from the user's existing $300 ledger."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .engine import Engine, valid_quote
from .day_wave import NAME as DAY_WAVE, config as day_config
from .researched import CANDIDATES, closed_features, from_feature, masks
from .store import Store


class ShadowStore(Store):
    def save(self, state, events=(), messages=()):
        super().save(state, events, ())
    def enqueue(self, message):
        pass  # Only the main account's Telegram carries the comparison summary.


class DayWaveStore(Store):
    """Persist trade notifications with this ledger, then relay at least once."""
    prefix = '🌊 موجة اليوم — حساب تجريبي مستقل، ليس المحفظة الأساسية\n'

    def save(self, state, events=(), messages=()):
        super().save(state, events, [self.prefix+m for m in messages])

    def enqueue(self, message):
        super().enqueue(self.prefix+message)


class Research:
    def __init__(self, cfg, main):
        self.cfg, self.main = cfg, main
        self.diagnostics = {"reason": "لم يكتمل فحص السوق بعد", "at": None}
        self.policy = json.loads(Path(__file__).with_name('policy.json').read_text())
        approved = self.policy.get('approved_strategy')
        if approved is not None and (approved not in CANDIDATES or not all(self.policy['promotion_checks'].values())):
            raise ValueError('Invalid strategy promotion policy')
        self.approved = approved
        self.main.s['research_blocked'] = approved is None
        self.main.store.save(self.main.s)
        self.shadows = {name: Engine(day_config(cfg) if name == DAY_WAVE else cfg,
                            (DayWaveStore if name == DAY_WAVE else ShadowStore)(str(Path(cfg.data_dir)/f'shadow_{name}.db')))
                        for name in CANDIDATES}

    def announce(self):
        if self.main.store.meta('research_policy_version') != self.policy['version']:
            self.main.store.enqueue('🔬 نتائج بحث الاستراتيجيات\n'
                'لم تجتز أي من القواعد الست شروط الاعتماد بعد الرسوم والانزلاق.\n'
                'المحفظة الأساسية: لا دخول جديد، مع استمرار إدارة خروج المراكز القائمة.\n'
                'أضيفت موجة اليوم: اختراق ثم أول تراجع ثم تأكيد؛ حساب مستقل 300 USDT افتراضي وتجربة 7 أيام.\n'
                'موجة اليوم خاسرة أيضًا في الاختبار التاريخي بعد التكاليف وفرصها قليلة. البيانات مستخدمة سابقًا؛ هذا اختبار أمامي وليس اعتمادًا للربحية.\n'
                'المخاطرة القصوى المخططة 0.5% للصفقة، مركزان، حد خسارة اليوم 2%. الخروج 23:45 UTC (02:45 الرياض) بأول سعر حديث متاح.\n'
                'صفقات موجة اليوم تصلك هنا مع تمييز حسابها. بقيت الحسابات الست السابقة للمقارنة.\n'
                f"توسع الفحص إلى أعلى {self.policy['live_pair_target']} زوج USDT مؤهل حسب السيولة (أو المتاح إذا قل العدد). نتائج الثمانية أزواج التاريخية لا تثبت أداء التوسع.\n"
                'أُلغي شرط وجود BTC فوق متوسطه للدخول في العملات الأخرى؛ بقي مانع هبوطه 2.5% أو أكثر خلال الساعة.\n'
                'أرسل /strategies لنتائج المقارنة. لا يوجد تفعيل تلقائي لاستراتيجية غير معتمدة.')
            self.main.store.set_meta('research_policy_version',self.policy['version'])

    def relay_day_messages(self):
        store = self.shadows[DAY_WAVE].store
        for item in store.pending():
            self.main.store.enqueue(item['message'])
            store.acknowledge(item['id'])

    def analyze(self, rows_by_symbol, quotes, now):
        frames = {s:closed_features(rows,int(now*1000)) for s,rows in rows_by_symbol.items()}
        self.diagnostics = {'at': now, 'valid_frames': sum(f is not None for f in frames.values()),
                            'reason': 'بيانات BTC غير مكتملة أو قديمة', 'btc_regime': None,
                            'candidates': {}, 'entered': {}}
        btc = frames.get('BTCUSDT')
        if btc is None:
            return []
        latest = btc.iloc[-1]
        # Live policy: each asset supplies its own trend filter; BTC only blocks sharp falls.
        regime = latest[4]/btc.iloc[-5][4]-1 > -0.025
        self.diagnostics.update(btc_regime=bool(regime), btc_close=float(latest[4]),
                                btc_sma200=float(latest['sma200']), btc_sma_gate_enabled=False,
                                btc_hour_change_pct=float((latest[4]/btc.iloc[-5][4]-1)*100))
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
        self.diagnostics['candidates'] = {name:len(items) for name,items in by_name.items()}
        for name,engine in self.shadows.items():
            self.diagnostics['entered'][name] = 0
            if self.main.s['paused'] or not all(valid_quote(quotes.get(s),now,self.cfg.stale_seconds) for s in engine.s['positions']):
                continue
            for signal in sorted(by_name[name],key=lambda s:(-s.score,s.symbol)):
                if engine.enter(signal,quotes.get(signal.symbol),now):
                    self.diagnostics['entered'][name] += 1
        day = self.shadows[DAY_WAVE]
        self.diagnostics['accounts'] = {n: {'open': len(e.s['positions']), 'closed': e.s['closed_trades'],
                                         'paused': e.s['paused'], 'halted': e.s['daily_halt'],
                                         'terminal': e.s['terminal']} for n,e in self.shadows.items()}
        self.diagnostics['primary_paused'] = self.main.s['paused']
        if self.main.s['paused'] or day.s['paused']:
            reason = 'إيقاف يدوي للدخول'
        elif day.s['terminal']:
            reason = day.s['terminal']
        elif day.s['daily_halt']:
            reason = 'حد خسارة اليوم'
        elif now % 86400 >= 23*3600:
            reason = 'انتهت نافذة الدخول اليومية 23:00 UTC'
        elif not regime:
            reason = 'هبوط BTC خلال الساعة بلغ 2.5% أو أكثر؛ إيقاف الدخول مؤقتًا'
        elif not by_name[DAY_WAVE]:
            reason = 'لم تكتمل إشارة الاختراق ثم أول تراجع ثم التأكيد في الأزواج المحللة'
        elif self.diagnostics['entered'][DAY_WAVE]:
            reason = 'تم تنفيذ دخول ورقي في هذه الدورة'
        else:
            reason = 'ظهرت إشارة؛ لم تنفذ بسبب قيود المحفظة أو السعر أو تكرار الإشارة'
        self.diagnostics['reason'] = reason
        self.relay_day_messages()
        return by_name[self.approved] if self.approved else []

    def monitor(self, quotes, now):
        for engine in self.shadows.values():
            engine.monitor(quotes,now)
        self.relay_day_messages()

    def report(self):
        lines = ['🔬 مقارنة الاستراتيجيات — حسابات ورقية منفصلة',
                 'المعتمد للمحفظة الأساسية: '+(self.approved or 'لا شيء؛ لا مداخل جديدة'),
                 f"اختبار الأقل خسارة خارج العينة: {self.policy['holdout_mean_week_return_pct']:+.2f}% متوسط أسبوعي.",
                 'المقارنة الأمامية الحالية (كل حساب بدأ بـ300):']
        for name,e in sorted(self.shadows.items(),key=lambda item:item[1].equity(),reverse=True):
            lines.append(f"{name}: {e.equity():.2f} USDT | {e.equity()-300:+.2f} | مكتملة {e.s['closed_trades']} | مفتوحة {len(e.s['positions'])}")
        day = self.shadows[DAY_WAVE]
        lines.append(f"🌊 موجة اليوم | نتيجة اليوم {day.equity()-day.s['day_equity']:+.2f} USDT | الحالة: {day.s['terminal'] or ('حد خسارة اليوم' if day.s['daily_halt'] else 'إيقاف يدوي' if self.main.s['paused'] else 'اختبار أمامي')}")
        stamp = self.diagnostics.get('at')
        checked = datetime.fromtimestamp(stamp, timezone.utc).strftime('%H:%M UTC') if stamp else 'لا يوجد'
        stale = ' (قديمة)' if stamp and time.time()-stamp > 120 else ''
        lines.append(f"آخر فحص {checked}{stale}: {self.diagnostics['reason']}")
        lines.append(f"شموع صالحة في آخر فحص: {self.diagnostics.get('valid_frames', 0)} | المستهدف {self.policy['live_pair_target']} زوج حسب السيولة")
        lines.append('هذه مقارنة تجريبية، لا تثبت ربحية ولا تستخدم المحفظة الأساسية. /pause يمنع دخول الجميع؛ المخارج تستمر.')
        return '\n'.join(lines)

    def close(self):
        for engine in self.shadows.values():
            engine.store.db.close()
