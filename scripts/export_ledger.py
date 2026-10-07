"""Read-only CSV export of durable paper fills; never opens a missing database."""
import argparse
import csv
import json
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default='data/ledger.db')
    parser.add_argument('--output', default='trades.csv')
    args = parser.parse_args()
    path = Path(args.database).resolve()
    connection = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
    fields = ['ts', 'kind', 'symbol', 'price', 'qty', 'fee', 'pnl', 'reason']
    with open(args.output, 'w', newline='', encoding='utf-8-sig') as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for (body,) in connection.execute("SELECT body FROM events WHERE kind IN ('buy','sell') ORDER BY id"):
            writer.writerow(json.loads(body))
    connection.close()
    print(f'Paper fills exported to {args.output}')


if __name__ == '__main__':
    main()
