import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class Store:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, job_key TEXT NOT NULL,
                    body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    error TEXT, cursor INTEGER NOT NULL DEFAULT 0, UNIQUE(kind, job_key)
                );
                CREATE TABLE IF NOT EXISTS intake_limits (
                    actor TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY (actor, bucket)
                );
                CREATE TABLE IF NOT EXISTS interactions (
                    id TEXT PRIMARY KEY, token TEXT NOT NULL, expires REAL NOT NULL
                );
            """)
            db.execute("UPDATE jobs SET state='queued' WHERE state='running' AND kind='event'")
            db.execute("""UPDATE jobs SET state='unknown', error='send_interrupted', body='{}'
                          WHERE state='running' AND kind='delivery'""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, kind, key, body):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT * FROM jobs WHERE kind=? AND job_key=?", (kind, key)).fetchone()
            if found:
                return found, False
            identifier = str(uuid4())
            db.execute("INSERT INTO jobs(id,kind,job_key,body) VALUES(?,?,?,?)",
                       (identifier, kind, key, json.dumps(body)))
            return db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone(), True

    def enqueue_event(self, key, body, interaction):
        actor = f'{body["platform"]}:{body["actor_external_id"]}'
        bucket = int(time.time() // 60)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE kind='event' AND job_key=?", (key,)).fetchone():
                return "duplicate"
            row = db.execute("""
                INSERT INTO intake_limits VALUES (?, ?, 1)
                ON CONFLICT(actor, bucket) DO UPDATE SET count=count+1 RETURNING count
            """, (actor, bucket)).fetchone()
            if row["count"] > 20:
                return "rate_limited"
            db.execute("INSERT INTO jobs(id,kind,job_key,body) VALUES(?, 'event', ?, ?)",
                       (str(uuid4()), key, json.dumps(body)))
            if interaction:
                db.execute("INSERT INTO interactions VALUES(?,?,?)", interaction)
            return "accepted"

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM interactions WHERE expires <= ?", (time.time(),))
            db.execute("DELETE FROM intake_limits WHERE bucket < ?", (int(time.time() // 60) - 1,))
            row = db.execute("""SELECT * FROM jobs WHERE state='queued' AND next_attempt<=?
                                ORDER BY rowid LIMIT 1""", (time.time(),)).fetchone()
            if row:
                db.execute("UPDATE jobs SET state='running', attempts=attempts+1 WHERE id=?", (row["id"],))
            return row

    def progress(self, identifier, cursor):
        with self.connection() as db:
            db.execute("UPDATE jobs SET cursor=? WHERE id=?", (cursor, identifier))

    def finish(self, identifier, state, error=None, delay=0, body=None):
        with self.connection() as db:
            db.execute("""UPDATE jobs SET state=?, error=?, next_attempt=?,
                          body=CASE WHEN ?='queued' THEN COALESCE(?,body) ELSE '{}' END WHERE id=?""",
                       (state, error, time.time() + delay, state, json.dumps(body) if body else None, identifier))

    def get(self, identifier):
        with self.connection() as db:
            return db.execute("SELECT * FROM jobs WHERE id=? AND kind='delivery'", (identifier,)).fetchone()

    def interaction(self, identifier):
        with self.connection() as db:
            row = db.execute("SELECT token FROM interactions WHERE id=? AND expires>?",
                             (identifier, time.time())).fetchone()
            return row["token"] if row else None
