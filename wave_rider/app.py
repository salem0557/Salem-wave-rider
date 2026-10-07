"""Background worker with health endpoints only; intentionally no UI."""
import fcntl
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import Config
from .service import Service
from .store import Store

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app):
    cfg = Config.from_env()
    if os.getenv('RAILWAY_ENVIRONMENT_ID') and not os.getenv('RAILWAY_VOLUME_MOUNT_PATH'):
        raise RuntimeError('Attach a Railway persistent volume at /data before running the experiment')
    if os.getenv('RAILWAY_VOLUME_MOUNT_PATH') and Path(cfg.data_dir).resolve() != Path(os.environ['RAILWAY_VOLUME_MOUNT_PATH']).resolve():
        raise RuntimeError('DATA_DIR must equal the Railway volume mount path')
    lock = open(Path(cfg.data_dir)/'worker.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('Another worker already owns this paper ledger') from None
    service = Service(cfg, Store(str(Path(cfg.data_dir)/'ledger.db')))
    app.state.service = service
    await service.start()
    try:
        yield
    finally:
        await service.close()
        lock.close()


app = FastAPI(title='Wave Rider Paper Worker', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.get('/healthz')
def health():
    service = app.state.service
    alive = time.time()-service.heartbeat < 90 and all(not task.done() for task in service.tasks[:2])
    return JSONResponse({'status': 'ok' if alive else 'stalled', 'mode': 'paper'}, status_code=200 if alive else 503)


@app.get('/readyz')
def readiness():
    # No balance, positions, secrets or controls are exposed over HTTP.
    ready = app.state.service.status()['market_ready']
    return JSONResponse({'market_ready': ready, 'mode': 'paper'}, status_code=200 if ready else 503)
