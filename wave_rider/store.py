"""Single-process durable ledger. Ledger and Telegram outbox commit atomically."""
import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, kind TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS outbox (
          id INTEGER PRIMARY KEY, ts REAL, message TEXT, sent_at REAL, attempts INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        """)

    def load(self):
        row = self.db.execute("SELECT body FROM state WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save(self, state, events=(), messages=()):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(1,?)", (json.dumps(state, allow_nan=False),))
            for event in events:
                self.db.execute("INSERT INTO events(ts,kind,body) VALUES(?,?,?)",
                                (event['ts'], event['kind'], json.dumps(event, allow_nan=False)))
            for message in messages:
                self.db.execute("INSERT INTO outbox(ts,message) VALUES(?,?)", (time.time(), message))

    def enqueue(self, message):
        with self.db:
            self.db.execute("INSERT INTO outbox(ts,message) VALUES(?,?)", (time.time(), message))

    def pending(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY id LIMIT 10")]

    def acknowledge(self, message_id):
        with self.db:
            self.db.execute("UPDATE outbox SET sent_at=? WHERE id=?", (time.time(), message_id))

    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, str(value)))

    def events(self, limit=100):
        return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM events ORDER BY id DESC LIMIT ?", (limit,))]
