"""Session memory kept in SQLite (standard library): one JSON document per session.

Memory must hold JSON values only (dicts, lists, strings, numbers, booleans, None).
"""
import json
import sqlite3
import threading
import time


class SessionStore:
    def __init__(self, path=":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS sessions "
                         "(id TEXT PRIMARY KEY, memory TEXT NOT NULL, updated REAL NOT NULL)")
        self._db.commit()
        self._lock = threading.Lock()

    def load(self, session):
        with self._lock:
            row = self._db.execute("SELECT memory FROM sessions WHERE id = ?", (session,)).fetchone()
        if row is None:
            return {}
        try:
            memory = json.loads(row[0])
        except ValueError:
            return {}
        return memory if isinstance(memory, dict) else {}

    def save(self, session, memory):
        """Raises TypeError or ValueError when memory holds something that is not JSON."""
        data = json.dumps(memory, ensure_ascii=False)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO sessions (id, memory, updated) VALUES (?, ?, ?)",
                             (session, data, time.time()))
            self._db.commit()

    def forget(self, session):
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE id = ?", (session,))
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()
