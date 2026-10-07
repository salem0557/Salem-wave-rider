import math
import time
from collections import defaultdict, deque

import httpx


class MarketError(Exception):
    pass


class Binance:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = httpx.AsyncClient(base_url=cfg.binance_base_url, timeout=15)
        self.blocked_until = 0
        self.universe = {}
        self.quotes = {}
        self.history = defaultdict(lambda: deque(maxlen=45))
        self.last_scan = 0
        self.last_quotes = 0
        self.last_universe = 0
        self.last_deep = {}
        self.health_error = None
        self.weight = 0

    async def get(self, path, params=None):
        if time.time() < self.blocked_until:
            raise MarketError('Binance backoff active; no new data')
        # Public market-data endpoints only; there is no signed/order endpoint.
        if path not in {'exchangeInfo', 'ticker/24hr', 'ticker/bookTicker', 'klines'}:
            raise ValueError('Unsupported public endpoint')
        try:
            response = await self.client.get('/api/v3/'+path, params=params)
        except httpx.HTTPError:
            raise MarketError('Binance network unavailable') from None
        if response.status_code in (418, 429, 451, 403):
            try:
                delay = max(60, float(response.headers.get('Retry-After', '60')))
            except ValueError:
                delay = 60
            if response.status_code in (451, 403):
                delay = 3600
            if response.status_code == 418:
                delay = max(delay, 3600)
            self.blocked_until = time.time()+delay
            raise MarketError(f'Binance HTTP {response.status_code}; paused for {delay:.0f}s. Check service eligibility and deployment region.')
        if response.status_code != 200:
            raise MarketError(f'Binance HTTP {response.status_code}')
        self.weight = int(response.headers.get('x-mbx-used-weight-1m', '0'))
        if self.weight > 4500:
            self.blocked_until = time.time()+60
        return response.json()

    async def refresh_universe(self):
        payload = await self.get('exchangeInfo')
        self.universe = {s['symbol']: s for s in payload['symbols']
                         if s['status'] == 'TRADING' and s.get('isSpotTradingAllowed', False)}
        if not self.universe:
            raise MarketError('No tradable spot symbols received')
        self.last_universe = time.time()

    async def books(self):
        requested_at = time.time()
        payload = await self.get('ticker/bookTicker')
        result = {}
        for row in payload:
            try:
                bid, ask = float(row['bidPrice']), float(row['askPrice'])
                qty = float(row['askQty'])
                if all(math.isfinite(x) for x in (bid, ask, qty)) and bid > 0 and ask >= bid:
                    result[row['symbol']] = dict(bid=bid, ask=ask, ask_qty=qty, ts=requested_at)
            except (KeyError, TypeError, ValueError):
                continue
        if not result:
            raise MarketError('Empty order book response')
        self.quotes, self.last_quotes = result, requested_at
        return result

    async def shortlist(self):
        if time.time()-self.last_universe > 3600:
            await self.refresh_universe()
        rows = await self.get('ticker/24hr')
        now = time.time()
        candidates = []
        stable = {'USDC', 'FDUSD', 'TUSD', 'USDP', 'DAI', 'USD1', 'USDE', 'USDD', 'EURI', 'EUR', 'AEUR', 'PAXG'}
        for row in rows:
            symbol = row['symbol']
            info = self.universe.get(symbol)
            if info is None:
                continue
            price, volume = float(row['lastPrice']), float(row['quoteVolume'])
            if not math.isfinite(price) or not math.isfinite(volume) or price <= 0:
                continue
            hist = self.history[symbol]
            hist.append((now, price))
            # All spot pairs are observed; paper entries are only USDT pairs.
            if info['quoteAsset'] != 'USDT' or info['baseAsset'] in stable or volume < self.cfg.min_quote_volume:
                continue
            previous = [v for t, v in hist if 60 <= now-t <= 360]
            momentum = (price/previous[-1]-1)*100 if previous else 0
            daily = float(row['priceChangePercent'])
            if daily < -15 or daily > 80:
                continue
            score = momentum*10 + min(max(daily, 0), 20)/10 + math.log10(max(volume, 1))/10
            candidates.append((score, symbol))
        candidates.sort(reverse=True)
        # 12 momentum leaders + 8 rotating eligible symbols prevent permanent blind spots.
        selected = [s for _, s in candidates[:12]]
        rotation = sorted((s for _, s in candidates if s not in selected), key=lambda s: self.last_deep.get(s, 0))
        selected += rotation[:8]
        for s in selected:
            self.last_deep[s] = now
        self.last_scan = now
        return selected, len(rows), len(candidates)

    async def candles(self, symbol):
        return await self.get('klines', {'symbol': symbol, 'interval': '1m', 'limit': 100})

    def min_notional(self, symbol):
        return max([float(f.get('minNotional', 0)) for f in self.universe[symbol]['filters']
                    if f['filterType'] in ('NOTIONAL', 'MIN_NOTIONAL')] or [5.0])

    async def close(self):
        await self.client.aclose()
