import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    initial_cash: float = 300.0
    target_equity: float = 600.0
    experiment_days: int = 7
    risk_per_trade: float = 0.01
    max_position_fraction: float = 0.20
    max_positions: int = 3
    daily_loss_limit: float = 0.05
    max_drawdown: float = 0.15
    fee_rate: float = 0.001
    slippage: float = 0.0005
    max_spread: float = 0.002
    min_quote_volume: float = 5_000_000
    stale_seconds: float = 30
    scan_seconds: int = 30
    monitor_seconds: int = 5
    max_hold_seconds: int = 6*3600
    intraday_flatten: bool = False
    cooldown_seconds: int = 3600
    data_dir: str = "./data"
    binance_base_url: str = "https://api.binance.com"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_admin_id: str = ""
    research_enabled: bool = False
    candle_interval: str = "1m"
    candle_limit: int = 100

    @classmethod
    def from_env(cls):
        if os.getenv("TRADING_MODE", "paper").lower() != "paper":
            raise ValueError("Only paper mode is implemented; real-money trading is disabled")
        base = os.getenv("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")
        if base != "https://api.binance.com":
            raise ValueError("Use the official Binance endpoint; regional restrictions must not be bypassed")
        config = cls(data_dir=os.getenv("DATA_DIR", "./data"), binance_base_url=base,
                     research_enabled=True, candle_interval="5m", candle_limit=320,
                     telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                     telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
                     telegram_admin_id=os.getenv("TELEGRAM_ADMIN_USER_ID", ""))
        Path(config.data_dir).mkdir(parents=True, exist_ok=True)
        return config
