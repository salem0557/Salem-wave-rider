import asyncio
import logging
import time
from dataclasses import asdict

from .engine import Engine, day_key, valid_quote
from .market import Binance, MarketError
from .strategy import evaluate
from .telegram import Telegram

log = logging.getLogger(__name__)


class Service:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.engine = Engine(cfg, store)
        self.market = Binance(cfg)
        self.telegram = Telegram(cfg, store, self.engine, self.status_text)
        self.last_error = None
        self.heartbeat = time.time()
        self.scanned_count = 0
        self.eligible_count = 0
        self.last_alert = 0
        self.last_status_log = 0
        self.tasks = []

    def status(self):
        now = time.time()
        return {
            'mode': 'paper', 'real_orders_enabled': False,
            'market_ready': now-self.market.last_quotes <= self.cfg.stale_seconds and now-self.market.last_scan <= 120,
            'last_quotes_at': self.market.last_quotes or None,
            'last_scan_at': self.market.last_scan or None,
            'spot_pairs': len(self.market.universe), 'observed_pairs': self.scanned_count,
            'eligible_usdt_pairs': self.eligible_count,
            'error': self.last_error,
            'telegram_configured': bool(self.cfg.telegram_token and self.cfg.telegram_chat_id),
        }

    def status_text(self):
        s = self.status()
        return (f"المصدر: Binance Spot | البيانات: {'حديثة' if s['market_ready'] else 'غير جاهزة / قديمة'}\n"
                f"الأزواج الفورية: {s['spot_pairs']} | المؤهلة USDT: {s['eligible_usdt_pairs']}\n"
                f"فحص شموع: حتى 20 زوجًا كل دورة (الأعلى زخمًا + تناوب)\n"
                f"الخطأ: {s['error'] or 'لا يوجد'}")

    def error(self, message):
        self.last_error = message
        if time.time()-self.last_alert > 900:
            log.warning('%s', message)
            self.store.enqueue('⚠️ '+message+'\nلا دخول جديد دون بيانات حديثة؛ الخروج ينتظر أسعارًا موثوقة.')
            self.last_alert = time.time()

    async def scanner(self):
        while True:
            try:
                symbols, observed, eligible = await self.market.shortlist()
                self.scanned_count, self.eligible_count = observed, eligible
                now = time.time()
                if now-self.market.last_quotes > self.cfg.stale_seconds:
                    await asyncio.sleep(2)
                    continue
                self.engine.start(now)
                signals = []
                for symbol in symbols:
                    rows = await self.market.candles(symbol)
                    signal = evaluate(symbol, rows, int(time.time()*1000))
                    if signal:
                        signals.append(signal)
                    await asyncio.sleep(0.15)
                self.last_error = None
                for signal in sorted(signals, key=lambda s: s.score, reverse=True):
                    now = time.time()
                    # Avoid sizing against stale held positions after a partial data failure.
                    if not all(valid_quote(self.market.quotes.get(sym), now, self.cfg.stale_seconds)
                               for sym in self.engine.s['positions']):
                        break
                    self.store.save(self.engine.s, events=[dict(ts=now, kind='signal', **asdict(signal))])
                    if now*1000-signal.candle_ms > 150_000:
                        continue
                    self.engine.enter(signal, self.market.quotes.get(signal.symbol), now,
                                      self.market.min_notional(signal.symbol))
                if time.time()-self.last_status_log >= 60:
                    log.info('Binance scan complete: market_ready=%s spot_pairs=%d eligible_usdt=%d analyzed=%d signals=%d',
                             self.status()['market_ready'], len(self.market.universe), eligible, len(symbols), len(signals))
                    self.last_status_log = time.time()
            except asyncio.CancelledError:
                raise
            except MarketError as exc:
                self.error(str(exc))
            except Exception:
                # Never emit raw HTTP exceptions that could include secret URLs.
                log.exception('Scanner processing error')
                self.error('تعذر معالجة بيانات السوق؛ تم تعطيل هذه الدورة.')
            await asyncio.sleep(self.cfg.scan_seconds)

    async def monitor(self):
        while True:
            self.heartbeat = time.time()
            try:
                quotes = await self.market.books()
                now = time.time()
                self.engine.monitor(quotes, now)
                missing = [s for s in self.engine.s['positions'] if not valid_quote(quotes.get(s), now, self.cfg.stale_seconds)]
                if missing:
                    self.error('أسعار مراكز مفتوحة غير متاحة: '+', '.join(missing))
                if self.engine.s['terminal'] and not self.engine.s['positions'] and not self.store.meta('final_report_sent'):
                    self.store.enqueue('🏁 التقرير النهائي للتجربة\n'+self.engine.report())
                    self.store.set_meta('final_report_sent', '1')
            except asyncio.CancelledError:
                raise
            except MarketError as exc:
                self.error(str(exc))
            except Exception:
                log.exception('Monitor processing error')
                self.error('تعذر تحديث تقييم المحفظة.')
            today = day_key(time.time())
            if self.store.meta('last_report_day') != today:
                self.store.enqueue('📊 التقرير اليومي UTC\n'+self.engine.report())
                self.store.set_meta('last_report_day', today)
            await asyncio.sleep(self.cfg.monitor_seconds)

    async def start(self):
        self.tasks = [asyncio.create_task(self.scanner(), name='scanner'),
                      asyncio.create_task(self.monitor(), name='monitor'),
                      asyncio.create_task(self.telegram.run(), name='telegram')]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.market.close()
        await self.telegram.close()
        self.store.db.close()
