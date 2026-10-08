import time
import httpx
import pytest

from wave_rider.config import Config
from wave_rider.engine import Engine
from wave_rider.market import Binance, MarketError
from wave_rider.store import Store
from wave_rider.telegram import Telegram


@pytest.mark.asyncio
async def test_451_stops_retries_and_never_uses_fallback():
    market = Binance(Config())
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(451, json={'msg': 'restricted'})
    await market.client.aclose()
    market.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://api.binance.com')
    with pytest.raises(MarketError, match='451'):
        await market.books()
    with pytest.raises(MarketError, match='backoff'):
        await market.books()
    assert len(calls) == 1
    assert not market.quotes
    await market.close()


@pytest.mark.asyncio
async def test_429_respects_retry_after():
    market = Binance(Config())
    await market.client.aclose()
    market.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
        429, headers={'Retry-After': '120'}, json={})), base_url='https://api.binance.com')
    with pytest.raises(MarketError):
        await market.books()
    assert market.blocked_until >= time.time()+119
    await market.close()


@pytest.mark.asyncio
async def test_telegram_authorization_pause_and_durable_outbox(tmp_path):
    cfg = Config(telegram_token='test-secret', telegram_chat_id='123')
    store = Store(str(tmp_path/'ledger.db'))
    engine = Engine(cfg, store)
    bot = Telegram(cfg, store, engine, lambda: 'healthy')
    message = {'message': {'chat': {'id': 999, 'type': 'private'}, 'from': {'id': 999}, 'text': '/pause'}}
    await bot.handle(message)
    assert not engine.s['paused']
    message['message']['chat']['id'] = 123
    await bot.handle(message)
    assert engine.s['paused']
    assert len(store.pending()) == 1
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': 1}})
    await bot.client.aclose()
    bot.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await bot.flush()
    assert not store.pending()
    assert len(requests) == 1
    await bot.close()


@pytest.mark.asyncio
async def test_failed_telegram_delivery_keeps_pending_and_hides_token(tmp_path):
    cfg = Config(telegram_token='very-secret', telegram_chat_id='123')
    store = Store(str(tmp_path/'ledger.db'))
    store.enqueue('paper trade')
    bot = Telegram(cfg, store, Engine(cfg, store), lambda: '')
    await bot.client.aclose()
    def handler(request):
        raise httpx.ConnectError('bad https://api.telegram.org/botvery-secret', request=request)
    bot.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError) as exc:
        await bot.flush()
    assert 'very-secret' not in str(exc.value)
    assert len(store.pending()) == 1
    await bot.close()


@pytest.mark.asyncio
async def test_full_scanner_pipeline_persists_signal_and_paper_fill(tmp_path):
    import asyncio
    from wave_rider.service import Service
    from tests.test_strategy import bars

    store = Store(str(tmp_path/'integration.db'))
    service = Service(Config(), store)
    current_minute = int(time.time()//60)*60_000
    rows = bars()
    shift = current_minute-100*60_000
    for row in rows:
        row[0] += shift
        row[6] += shift
    def handler(request):
        path = request.url.path
        if path.endswith('exchangeInfo'):
            payload = {'symbols': [{'symbol': 'BTCUSDT', 'baseAsset': 'BTC', 'quoteAsset': 'USDT',
                                   'status': 'TRADING', 'isSpotTradingAllowed': True,
                                   'filters': [{'filterType': 'NOTIONAL', 'minNotional': '5'}]}]}
        elif path.endswith('ticker/24hr'):
            payload = [{'symbol': 'BTCUSDT', 'lastPrice': '101.5', 'quoteVolume': '20000000', 'priceChangePercent': '2'}]
        elif path.endswith('ticker/bookTicker'):
            payload = [{'symbol': 'BTCUSDT', 'bidPrice': '101.5', 'askPrice': '101.51', 'askQty': '100'}]
        elif path.endswith('klines'):
            payload = rows
        else:
            raise AssertionError('Unexpected endpoint')
        return httpx.Response(200, json=payload)
    await service.market.client.aclose()
    service.market.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://api.binance.com')
    await service.market.books()
    task = asyncio.create_task(service.scanner())
    try:
        for _ in range(100):
            if service.engine.s['positions']:
                break
            await asyncio.sleep(0.01)
        assert 'BTCUSDT' in service.engine.s['positions']
        assert service.engine.s['cash'] == pytest.approx(240)
        assert {e['kind'] for e in store.events()} == {'buy', 'signal'}
        assert service.status()['market_ready']
        assert len(store.pending()) == 2  # Experiment start + entry.
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_group_commands_require_admin_and_old_commands_ignored(tmp_path):
    cfg = Config(telegram_token='test', telegram_chat_id='-123', telegram_admin_id='77')
    store = Store(str(tmp_path/'ledger.db'))
    engine = Engine(cfg, store)
    bot = Telegram(cfg, store, engine, lambda: '')
    msg = {'message': {'chat': {'id': -123, 'type': 'group'}, 'from': {'id': 99}, 'text': '/pause', 'date': time.time()}}
    await bot.handle(msg)
    assert not engine.s['paused']
    msg['message']['from']['id'] = 77
    msg['message']['date'] = time.time()-600
    await bot.handle(msg)
    assert not engine.s['paused']
    msg['message']['date'] = time.time()
    await bot.handle(msg)
    assert engine.s['paused']
    await bot.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [90, 45])
async def test_research_scans_top70_liquid_pairs_or_reports_available(count):
    market = Binance(Config())
    market.research_pair_limit = 70
    symbols = ['BTCUSDT']+[f'COIN{i}USDT' for i in range(count-1)]
    market.universe = {s: {'baseAsset': s[:-4], 'quoteAsset': 'USDT'} for s in symbols}
    market.universe['USDCUSDT'] = {'baseAsset': 'USDC', 'quoteAsset': 'USDT'}
    market.universe['ILLIQUIDUSDT'] = {'baseAsset': 'ILLIQUID', 'quoteAsset': 'USDT'}
    market.last_universe = time.time()
    rows = [{'symbol': s, 'lastPrice': '100', 'quoteVolume': str(200_000_000-i*1_000_000),
             'priceChangePercent': '2'} for i,s in enumerate(symbols)]
    rows += [{'symbol': 'USDCUSDT', 'lastPrice': '1', 'quoteVolume': '999999999', 'priceChangePercent': '0'},
             {'symbol': 'ILLIQUIDUSDT', 'lastPrice': '1', 'quoteVolume': '100', 'priceChangePercent': '2'}]
    async def get(path, params=None):
        assert path=='ticker/24hr'
        return list(reversed(rows))  # Exchange response order must not control selection.
    market.get = get
    selected, observed, eligible = await market.shortlist()
    assert selected == symbols[:70]
    assert len(selected)==min(count,70) and eligible==count
    assert observed==count+2
    await market.close()
