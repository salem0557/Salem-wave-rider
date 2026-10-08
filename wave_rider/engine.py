import math
import time
from datetime import datetime, timezone

from .config import Config
from .store import Store
from .strategy import Signal


def day_key(now):
    return datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')


class Engine:
    def __init__(self, cfg: Config, store: Store, now=None):
        self.cfg, self.store = cfg, store
        now = time.time() if now is None else now
        self.s = store.load() or {
            'cash': cfg.initial_cash, 'initial_cash': cfg.initial_cash, 'started_at': None,
            'positions': {}, 'cooldowns': {}, 'seen': {}, 'realized_pnl': 0.0,
            'fees': 0.0, 'peak_equity': cfg.initial_cash, 'day': day_key(now),
            'day_equity': cfg.initial_cash, 'daily_halt': False,
            'paused': False, 'terminal': None, 'closed_trades': 0, 'wins': 0,
        }
        self.store.save(self.s)

    def equity(self):
        # Conservative marked liquidation value: bid, slippage and exit fees.
        return self.s['cash'] + sum(p['qty'] * p['mark'] * (1-self.cfg.slippage) * (1-self.cfg.fee_rate)
                                     for p in self.s['positions'].values())

    def report(self):
        s, eq = self.s, self.equity()
        started = s['started_at']
        remaining = 'لم تبدأ' if started is None else f"{max(0, self.cfg.experiment_days-(time.time()-started)/86400):.1f} يوم"
        return (f"🧪 تقرير تداول ورقي — USDT ≈ دولار\n"
                f"القيمة التقديرية: {eq:.2f} | النقد: {s['cash']:.2f}\n"
                f"النتيجة الكلية: {eq-s['initial_cash']:+.2f} ({eq/s['initial_cash']-1:+.2%})\n"
                f"المحقق: {s['realized_pnl']:+.2f} | الرسوم: {s['fees']:.3f}\n"
                f"غير المحقق بعد تقدير الخروج: {eq-s['initial_cash']-s['realized_pnl']:+.2f}\n"
                f"التراجع الحالي من القمة: {1-eq/s['peak_equity']:.2%} | نتيجة اليوم: {eq-s['day_equity']:+.2f}\n"
                f"صفقات مفتوحة: {len(s['positions'])} | مكتملة: {s['closed_trades']} | رابحة: {s['wins']}\n"
                f"الهدف التجريبي: {self.cfg.target_equity:.0f} (غير مضمون) | المتبقي: {remaining}\n"
                f"الحالة: {s['terminal'] or ('إيقاف يدوي' if s['paused'] else 'حد خسارة يومي' if s['daily_halt'] else 'لا استراتيجية معتمدة؛ المخارج مستمرة' if s.get('research_blocked') else 'جاهز')}\n"
                'التقييم بآخر سعر معروف؛ انقطاع البيانات يجعل التقييم قديمًا.')

    def positions_report(self):
        if not self.s['positions']:
            return '🧪 لا توجد صفقات ورقية مفتوحة.'
        return '\n\n'.join(f"🧪 {sym}\nالكمية {p['qty']:.8g} | الدخول {p['entry']:.8g}\n"
                           f"آخر bid {p['mark']:.8g} | وقف {p['stop']:.8g}\nالاستراتيجية {p['strategy']}"
                           for sym, p in self.s['positions'].items())

    def pause(self, paused):
        self.s['paused'] = paused
        self.store.save(self.s)
        return 'توقفت عمليات الدخول؛ إدارة الخروج مستمرة.' if paused else 'تم السماح بالدخول إذا اجتازت الفرصة حدود المخاطرة.'

    def _limits(self, now):
        s, eq = self.s, self.equity()
        if s['day'] != day_key(now):
            s.update(day=day_key(now), day_equity=eq, daily_halt=False)
        s['peak_equity'] = max(s['peak_equity'], eq)
        if not s['daily_halt'] and eq <= s['day_equity'] * (1-self.cfg.daily_loss_limit):
            s['daily_halt'] = True
            self.store.enqueue(f'🛑 حد خسارة اليوم {self.cfg.daily_loss_limit:.0%}: إيقاف الدخول وإغلاق المراكز عند توفر أسعار حديثة.')
        terminal = s['terminal']
        if s['started_at'] is not None and now-s['started_at'] >= self.cfg.experiment_days*86400:
            terminal = terminal or 'انتهت التجربة الأسبوعية'
        if eq <= s['peak_equity'] * (1-self.cfg.max_drawdown):
            terminal = terminal or f'حد التراجع الكلي {self.cfg.max_drawdown:.0%}'
        if eq >= self.cfg.target_equity:
            terminal = terminal or 'بلوغ الهدف التجريبي'
        if terminal and not s['terminal']:
            s['terminal'] = terminal
            self.store.enqueue(f'🛑 {terminal}: لا صفقات جديدة. إغلاق المراكز بأسعار السوق المتاحة.\n' + self.report())

    def enter(self, signal: Signal, quote, now, min_notional=5.0):
        s, cfg = self.s, self.cfg
        self._limits(now)
        if s['paused'] or s['daily_halt'] or s['terminal'] or s.get('research_blocked'):
            self.store.save(s)
            return False
        if cfg.intraday_flatten and (now % 86400 >= 23*3600 or any(day_key(p['opened_at']) != day_key(now) for p in s['positions'].values())):
            return False
        if signal.symbol in s['positions'] or len(s['positions']) >= cfg.max_positions:
            return False
        if s['cooldowns'].get(signal.symbol, 0) > now or s['seen'].get(signal.symbol) == signal.candle_ms:
            return False
        if not valid_quote(quote, now, cfg.stale_seconds):
            return False
        if (quote['ask']-quote['bid'])/quote['bid'] > cfg.max_spread:
            return False
        # No chasing a signal after the next price has already run away.
        if abs(quote['ask']/signal.price-1) > 0.01:
            return False
        eq = self.equity()
        entry = quote['ask']*(1+cfg.slippage)
        stop = entry*(1-signal.stop_fraction)
        cost_per_unit = entry*(1+cfg.fee_rate)
        loss_per_unit = cost_per_unit-stop*(1-cfg.slippage)*(1-cfg.fee_rate)
        qty = min(eq*cfg.risk_per_trade/loss_per_unit,
                  eq*cfg.max_position_fraction/cost_per_unit,
                  s['cash']/cost_per_unit,
                  quote.get('ask_qty', 0)*0.1)
        # Limit to 10% of displayed top-of-book liquidity. Quantities are fractional in paper mode.
        if not math.isfinite(qty) or qty*entry < min_notional:
            return False
        fee, cost = qty*entry*cfg.fee_rate, qty*cost_per_unit
        s['cash'] -= cost
        s['fees'] += fee
        s['positions'][signal.symbol] = {
            'qty': qty, 'initial_qty': qty, 'entry': entry, 'mark': quote['bid'],
            'mark_at': now, 'cost': cost, 'stop': stop, 'initial_stop': stop,
            'peak': quote['bid'], 'risk_distance': entry-stop,
            'opened_at': now, 'partial': False, 'strategy': signal.strategy, 'realized': 0.0,
        }
        s['seen'][signal.symbol] = signal.candle_ms
        s['started_at'] = s['started_at'] if s['started_at'] is not None else now
        event = dict(ts=now, kind='buy', symbol=signal.symbol, price=entry, qty=qty, fee=fee, reason=signal.reason)
        msg = (f"🧪 شراء ورقي {signal.symbol}\nالسعر {entry:.8g} | القيمة {qty*entry:.2f} USDT\n"
               f"الوقف {stop:.8g} | جني نصف المركز عند {entry+2*(entry-stop):.8g}\n"
               f"{signal.reason}\nرسوم {fee:.4f} + انزلاق {cfg.slippage:.2%}. لا أمر حقيقي.")
        self.store.save(s, [event], [msg])
        return True

    def start(self, now):
        if self.s['started_at'] is None:
            self.s['started_at'] = now
            self.store.save(self.s, messages=['🧪 بدأت تجربة 7 أيام برصيد 300 USDT افتراضي. لا ضمان للوصول إلى 600.'])

    def exit(self, symbol, bid, now, fraction, reason):
        s, cfg = self.s, self.cfg
        p = s['positions'][symbol]
        qty = p['qty']*fraction
        price = bid*(1-cfg.slippage)
        fee = qty*price*cfg.fee_rate
        cost = p['cost']*fraction
        proceeds = qty*price-fee
        pnl = proceeds-cost
        s['cash'] += proceeds
        s['fees'] += fee
        s['realized_pnl'] += pnl
        p['realized'] += pnl
        p['qty'] -= qty
        p['cost'] -= cost
        if fraction == 1:
            s['closed_trades'] += 1
            s['wins'] += int(p['realized'] > 0)
            del s['positions'][symbol]
            s['cooldowns'][symbol] = now+cfg.cooldown_seconds
        else:
            p['partial'] = True
            # Break-even including estimated round-trip costs, never lower the stop.
            p['stop'] = max(p['stop'], p['entry']*(1+cfg.fee_rate)/((1-cfg.slippage)*(1-cfg.fee_rate)))
        event = dict(ts=now, kind='sell', symbol=symbol, price=price, qty=qty, fee=fee, pnl=pnl, reason=reason)
        msg = (f"🧪 {'خروج' if fraction == 1 else 'جني نصف المركز'} ورقي {symbol}\n"
               f"السعر {price:.8g} | الصافي المحقق {pnl:+.3f} USDT\n"
               f"السبب: {reason}\nقيمة المحفظة التقديرية: {self.equity():.2f} USDT")
        self.store.save(s, [event], [msg])

    def monitor(self, quotes, now):
        # Update every mark before checking portfolio-level limits.
        for sym, p in self.s['positions'].items():
            q = quotes.get(sym)
            if valid_quote(q, now, self.cfg.stale_seconds):
                p['mark'], p['mark_at'] = q['bid'], now
        self._limits(now)
        for sym, p in list(self.s['positions'].items()):
            q = quotes.get(sym)
            if not valid_quote(q, now, self.cfg.stale_seconds):
                continue  # Never fabricate a fill while disconnected.
            bid = q['bid']
            if self.s['terminal'] or self.s['daily_halt']:
                self.exit(sym, bid, now, 1, self.s['terminal'] or 'حد الخسارة اليومي')
            elif bid <= p['stop']:
                # Gaps fill at the current bid, never at an unavailable stop price.
                self.exit(sym, bid, now, 1, 'وقف متحرك' if p['partial'] else 'وقف خسارة')
            elif self.cfg.intraday_flatten and (now % 86400 >= 23*3600+45*60 or day_key(now) != day_key(p['opened_at'])):
                self.exit(sym, bid, now, 1, 'إغلاق جلسة موجة اليوم UTC')
            elif not p['partial'] and bid >= p['entry']+2*p['risk_distance']:
                self.exit(sym, bid, now, 0.5, 'جني ربح عند 2R')
            elif now-p['opened_at'] >= self.cfg.max_hold_seconds:
                self.exit(sym, bid, now, 1, 'انتهاء الحد الزمني للمركز')
            if sym in self.s['positions']:
                p['peak'] = max(p['peak'], bid)
                if p['partial']:
                    p['stop'] = max(p['stop'], p['peak']-p['risk_distance'])
        self.store.save(self.s)


def valid_quote(q, now, max_age):
    return bool(q and all(math.isfinite(q.get(k, float('nan'))) for k in ('bid', 'ask', 'ts'))
                and q['bid'] > 0 and q['ask'] >= q['bid'] and 0 <= now-q['ts'] <= max_age)
