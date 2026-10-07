# Validation record

- **24 tests passed** locally, covering the paper engine, strategy, connectors, and an end-to-end mocked scanner-to-ledger flow. These check behavior, not trading profitability.
- Actual service startup passed: `/healthz` returned 200; `/readyz` correctly returned 503 while Binance was restricted; `/` and `/docs` returned 404 (no application page). CSV export and dependency consistency checks passed.
- The execution environment's request to `https://api.binance.com/api/v3/ping` returned **HTTP 451** (location restricted). No live price feed, historical performance result, or live Telegram delivery is claimed.
- The service deliberately preserves a degraded market state instead of generating market prices, moving to another exchange, or bypassing geographic restrictions.
- Source repository designated by the owner: `salem0557/Salem-wave-rider`.
- Railway credentials/tools were not confirmed available in this session. Railway configuration is included, but production deployment is not claimed.
- Telegram credentials are intentionally deferred by the user. The worker can run and store notifications until credentials are configured.

Run `pytest -q` and `python -m scripts.preflight` in the intended deployment environment before treating the experiment as active. A successful liveness check is not proof of market readiness.
