# Validation record — 2026-10-08

- **38 tests passed** locally: paper ledger accounting, fees, gap exits, partial exits, limits, persistence, Telegram authorization/delivery handling, causal features, live-vs-batch indicator parity, adverse intrabar order, and isolation/restart of the comparison accounts, daily cutoff/outage recovery, first-pullback expiration and durable labeled hybrid Telegram messages.
- Research inputs: **264 SHA-256-verified Binance public archives**, **771,072 complete 15-minute bars**, eight specified assets, January 2024 through September 2026. Reproducible manifest and results are in `reports/`.
- The selection protocol was committed before results. The 2025 candidate selection was committed before running the 2026 holdout. **No candidate passed promotion**; these are failed strategy tests, not a claim of profitable trading.
- Full assumptions, all selection results, final holdout, stress costs, and limitations: [RESEARCH_REPORT.md](RESEARCH_REPORT.md).
- Initial local HTTP smoke checks: `/healthz` 200, `/readyz` 503 under the local Binance location restriction, `/` and `/docs` 404. CSV export and dependency consistency checks passed.
- The local execution environment receives Binance API HTTP 451. Research uses Binance's separately published historical archives, not a live-price fallback. Live regional restrictions are not bypassed.
- Railway deployment of the preceding release was verified SUCCESS with a permanent `/data` volume, `market_ready=True`, and a confirmed Telegram delivery. Research-release deployment verification is performed against its own commit and deployment logs.
- No real orders, no Binance credentials, and no OpenRouter calls are implemented.

Run `pytest -q` to check behavior, and inspect `/status` plus `/strategies` for current runtime state. A liveness check alone is not proof of market readiness or profitability.

- Day Wave: one preregistered intraday adaptation; historical data reused and explicitly exploratory, no new holdout claim. Daily base/stress results: [DAY_WAVE_REPORT.md](DAY_WAVE_REPORT.md). No primary promotion.
- All 56 historical positive hybrid signals also matched a fresh 320-bar live-window calculation before market-regime/execution filtering.
- Dynamic live universe: tested selection of top70 eligible USDT pairs by quote volume, stable/illiquid exclusion, and honest counts when fewer than70 qualify.
- Live BTC filter regression: an ETH signal executes with BTC below its SMA200; a sharp BTC hourly drop still blocks entry. Historical reports retained with original rules; no new profitability claim.
