import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, cfg, store, engine, status):
        self.cfg, self.store, self.engine, self.status = cfg, store, engine, status
        self.client = httpx.AsyncClient(timeout=25)
        self.offset = int(store.meta('telegram_offset', '0'))
        self.last_error = None
        self.last_delivery = 0
        self.retry_at = 0

    async def api(self, method, payload):
        # Never log exception URLs: the Telegram API URL contains the bot token.
        try:
            response = await self.client.post(
                f'https://api.telegram.org/bot{self.cfg.telegram_token}/{method}', json=payload)
            data = response.json()
            if not data.get('ok'):
                if response.status_code == 429:
                    self.retry_at = time.time()+data.get('parameters', {}).get('retry_after', 60)
                raise RuntimeError(f'Telegram API error {response.status_code}')
            return data['result']
        except httpx.HTTPError:
            raise RuntimeError('Telegram network unavailable') from None
        except ValueError:
            raise RuntimeError('Telegram returned an invalid response') from None

    async def flush(self):
        if not self.cfg.telegram_chat_id or time.time() < self.retry_at:
            return
        for item in self.store.pending():
            await self.api('sendMessage', {'chat_id': self.cfg.telegram_chat_id,
                                          'text': f"#{item['id']}\n{item['message']}"[:4000]})
            self.store.acknowledge(item['id'])
            self.last_delivery = time.time()
            log.info('Telegram delivery confirmed: outbox_id=%s', item['id'])
        self.last_error = None

    async def handle(self, update):
        msg = update.get('message', {})
        text = msg.get('text', '').strip()
        chat = str(msg.get('chat', {}).get('id', ''))
        sender = str(msg.get('from', {}).get('id', ''))
        command = text.split()[0].split('@')[0].lower() if text else ''
        if not self.cfg.telegram_chat_id:
            if command in ('/start', '/id') and msg.get('chat', {}).get('type') == 'private':
                await self.api('sendMessage', {'chat_id': chat, 'text': f'رقم محادثتك: {chat}\nأضفه إلى TELEGRAM_CHAT_ID في Railway ثم أعد النشر. المحاكاة مستقلة عن الربط.'})
            return
        if chat != self.cfg.telegram_chat_id:
            return
        if self.cfg.telegram_admin_id and sender != self.cfg.telegram_admin_id:
            return
        if msg.get('chat', {}).get('type') != 'private' and not self.cfg.telegram_admin_id:
            return  # Groups require explicit admin identity for commands.
        if msg.get('date', time.time()) < time.time()-300:
            return  # Ignore old commands after downtime.
        if command in ('/start', '/help'):
            answer = ('🧪 Wave Rider — تداول ورقي فقط\n/status الحالة\n/report الرصيد والنتائج\n'
                      '/positions المراكز\n/pause إيقاف دخول جديد\n/resume استئناف الدخول\n'
                      '/id رقم المحادثة\nلا تحويل تلقائي إلى التداول الحقيقي، ولا ضمان ربح.')
        elif command == '/id':
            answer = f'Chat ID: {chat}\nUser ID: {sender}'
        elif command in ('/report', '/balance'):
            answer = self.engine.report()
        elif command == '/positions':
            answer = self.engine.positions_report()
        elif command == '/pause':
            answer = self.engine.pause(True)
        elif command == '/resume':
            answer = self.engine.pause(False)
        elif command == '/status':
            answer = self.status()+'\n'+self.engine.report()
        else:
            return
        self.store.enqueue(answer)

    async def run(self):
        if not self.cfg.telegram_token:
            log.info('Telegram not configured; notifications remain in the durable outbox')
            return
        if self.cfg.telegram_chat_id:
            self.store.enqueue('✅ تم تشغيل Salem Wave Rider وربط إشعارات تلغرام.\n'
                               '🧪 تداول ورقي فقط. لم يُفعّل تحليل OpenRouter بعد.\n'
                               'أرسل /status للحالة أو /report للتقرير أو /positions للمراكز.\n\n'
                               + self.engine.report())
        while True:
            try:
                await self.flush()
                if time.time() < self.retry_at:
                    await asyncio.sleep(5)
                    continue
                updates = await self.api('getUpdates', {'offset': self.offset, 'timeout': 5,
                                                        'allowed_updates': ['message']})
                for update in updates:
                    await self.handle(update)
                    self.offset = update['update_id']+1
                    self.store.set_meta('telegram_offset', self.offset)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.last_error = 'تعذر الاتصال بتلغرام؛ الإشعارات محفوظة لإعادة الإرسال.'
                log.warning('Telegram delivery/poll failed; retrying without exposing credentials')
                await asyncio.sleep(10)

    async def close(self):
        await self.client.aclose()
