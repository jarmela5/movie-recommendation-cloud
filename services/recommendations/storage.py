
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class RecommendationStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS latest_recommendations (
                    user_id INTEGER PRIMARY KEY,
                    profile_revision INTEGER NOT NULL,
                    generated_at TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    requested_limit INTEGER NOT NULL,
                    result_json TEXT NOT NULL
                )''')
                yield conn
        finally:
            conn.close()

    def save(self, user_id, revision, prompt, limit, result):
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False)
        generated_at = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            conn.execute('''INSERT INTO latest_recommendations
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    profile_revision=excluded.profile_revision,
                    generated_at=excluded.generated_at,
                    prompt=excluded.prompt,
                    requested_limit=excluded.requested_limit,
                    result_json=excluded.result_json
                WHERE excluded.profile_revision >= latest_recommendations.profile_revision
            ''', (user_id, revision, generated_at, prompt, limit, payload))

    def latest(self, user_id):
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM latest_recommendations WHERE user_id = ?', (user_id,)).fetchone()
        if row is None:
            return None
        return dict(profile_revision=row['profile_revision'], generated_at=row['generated_at'],
                    prompt=row['prompt'], requested_limit=row['requested_limit'],
                    result=json.loads(row['result_json']))
