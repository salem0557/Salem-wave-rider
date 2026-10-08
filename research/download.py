"""Download official public historical archives; never used as live API fallback."""
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import time
import urllib.request
import zipfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT/'data'/'research'


def fetch(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=40) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt+1)


def archive(job):
    symbol, month = job
    name = f'{symbol}-15m-{month}.zip'
    url = f'https://data.binance.vision/data/spot/monthly/klines/{symbol}/15m/{name}'
    path = CACHE/'archives'/name
    expected = fetch(url+'.CHECKSUM').decode().split()[0]
    payload = path.read_bytes() if path.exists() else fetch(url)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ValueError(f'Checksum mismatch: {name}')
    path.write_bytes(payload)
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), header=None)
    # Binance changed SPOT archives to microseconds in January 2025.
    for col in (0, 6):
        if int(df.iloc[0, col]) > 10**14:
            df[col] = df[col]//1000
    return symbol, df, {'file': name, 'url': url, 'sha256': actual, 'rows': len(df)}


def main():
    protocol = json.loads((ROOT/'research/protocol.json').read_text())
    months = pd.date_range(protocol['start'], protocol['end_exclusive'], freq='MS', inclusive='left').strftime('%Y-%m')
    (CACHE/'archives').mkdir(parents=True, exist_ok=True)
    jobs = [(s, m) for s in protocol['symbols'] for m in months]
    frames = {s: [] for s in protocol['symbols']}
    manifest = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for i, (symbol, frame, record) in enumerate(pool.map(archive, jobs), 1):
            frames[symbol].append(frame)
            manifest.append(record)
            if i % 24 == 0:
                print(f'Verified archives {i}/{len(jobs)}', flush=True)
    summary = {}
    for symbol, parts in frames.items():
        frame = pd.concat(parts).sort_values(0).reset_index(drop=True)
        if frame[0].duplicated().any():
            raise ValueError('Duplicate candle opens')
        prices = frame[[1,2,3,4]]
        if (prices <= 0).any().any() or frame.isna().any().any():
            raise ValueError('Invalid prices / missing values')
        if ((frame[2] < frame[[1,3,4]].max(axis=1)) | (frame[3] > frame[[1,2,4]].min(axis=1))).any():
            raise ValueError('Invalid OHLC bounds')
        expected = pd.date_range(protocol['start'], protocol['end_exclusive'], freq='15min', inclusive='left', tz='UTC')
        missing = len(expected)-len(frame)
        summary[symbol] = {'bars': len(frame), 'missing_bars': missing,
                           'start_ms': int(frame[0].iloc[0]), 'end_ms': int(frame[6].iloc[-1])}
        frame.to_pickle(CACHE/f'{symbol}.pkl')
    (ROOT/'reports/data_manifest.json').write_text(json.dumps({'summary': summary, 'archives': manifest}, indent=2)+'\n')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
