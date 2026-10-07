"""Safe deployment checks; displays only presence of credentials, never values."""
import asyncio
import os

from wave_rider.config import Config
from wave_rider.market import Binance, MarketError


async def main():
    cfg = Config.from_env()
    print('Mode: PAPER ONLY | Initial capital: 300 USDT | Duration: 7 days')
    print('Telegram token configured:', bool(cfg.telegram_token))
    print('Telegram recipient configured:', bool(cfg.telegram_chat_id))
    print('Persistent volume configured:', bool(os.getenv('RAILWAY_VOLUME_MOUNT_PATH')))
    market = Binance(cfg)
    try:
        await market.refresh_universe()
        await market.books()
        print('Binance market data ready. Active spot pairs:', len(market.universe))
    except MarketError as exc:
        print('Binance unavailable:', str(exc))
        raise SystemExit(1) from None
    finally:
        await market.close()


if __name__ == '__main__':
    asyncio.run(main())
