"""SQL document repository: local SQLite or persistent PostgreSQL over PostgREST.

Each circle is updated using an atomic version predicate, so concurrent confirms
cannot double-record a contribution. Private records never go to the browser.
"""
import copy
import json
import os
import sqlite3
import ssl
from pathlib import Path
import httpx
from .domain import AppError


class Store:
    def __init__(self, database=None, remote=None):
        self.remote = remote if remote is not None else bool(os.environ.get('HCF_SQL_URL'))
        self.path = database or os.environ.get('HCF_DATABASE', 'instance/halal-chit-fund.sqlite')
        if self.remote:
            self.url = os.environ['HCF_SQL_URL'].rstrip('/') + '/rest/v1/smoothfinance_records'
            self.headers = {'apikey': os.environ['HCF_SQL_PUBLIC_KEY'], 'x-hcf-backend-key': os.environ['HCF_SQL_BACKEND_KEY']}
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with self.connection() as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL)')

    def connection(self):
        return sqlite3.connect(self.path, timeout=15)

    def request(self, method, params=None, value=None):
        try:
            with httpx.Client(verify=ssl.create_default_context(), timeout=15) as client:
                response = client.request(method, self.url, params=params, json=value,
                                          headers={**self.headers, 'Prefer': 'return=representation'})
        except httpx.HTTPError:
            raise AppError('The SQL service is temporarily unavailable.', 503)
        if response.status_code == 409:
            return None
        if not response.is_success:
            # Do not log database URLs, credentials or response bodies.
            raise AppError('The SQL service could not complete the request.', 503)
        return response.json() if response.content else []

    def get(self, key):
        if self.remote:
            rows = self.request('GET', {'id': 'eq.' + key, 'select': 'payload,version'})
            return rows[0] if rows else None
        with self.connection() as db:
            row = db.execute('SELECT payload,version FROM records WHERE id=?', (key,)).fetchone()
        return {'payload': json.loads(row[0]), 'version': row[1]} if row else None

    def scan(self, prefix):
        if self.remote:
            result = []
            for offset in range(0, 10000, 500):
                rows = self.request('GET', {'id': 'like.' + prefix + '*', 'select': 'id,payload,version', 'order': 'id.asc', 'limit': '500', 'offset': str(offset)})
                result.extend(rows)
                if len(rows) < 500:
                    return result
            raise AppError('Record limit reached. Contact the organizer.', 409)
        with self.connection() as db:
            rows = db.execute('SELECT id,payload,version FROM records WHERE id LIKE ? ORDER BY id', (prefix + '%',)).fetchall()
        return [dict(id=r[0], payload=json.loads(r[1]), version=r[2]) for r in rows]

    def put(self, key, payload, version=None):
        if self.remote:
            if version is None:
                rows = self.request('POST', value={'id': key, 'payload': payload, 'version': 1})
            else:
                rows = self.request('PATCH', {'id': 'eq.' + key, 'version': 'eq.' + str(version)}, {'payload': payload, 'version': version + 1})
            return bool(rows)
        with self.connection() as db:
            if version is None:
                cursor = db.execute('INSERT OR IGNORE INTO records VALUES(?,?,1)', (key, json.dumps(payload)))
            else:
                cursor = db.execute('UPDATE records SET payload=?,version=version+1 WHERE id=? AND version=?', (json.dumps(payload), key, version))
            return cursor.rowcount == 1

    def update(self, key, transform, initial=None):
        for _ in range(8):
            row = self.get(key)
            if row is None and initial is None:
                raise AppError('Record not found.', 404)
            value = transform(copy.deepcopy(row['payload'] if row else initial))
            if self.put(key, value, row['version'] if row else None):
                return value
        raise AppError('Another request changed this record. Refresh and try again.', 409)
