"""Rate-aware recovery; no network/device identity changes on real targets."""
import json
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from enum import Enum


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Outcome(str, Enum):
    SUCCESS = 'SUCCESS'
    BAD_CREDENTIALS = 'BAD_CREDENTIALS'
    RATE_LIMIT = 'RATE_LIMIT'
    UNKNOWN = 'UNKNOWN'


@dataclass
class Detection:
    outcome: Outcome
    evidence_code: str
    retry_after_seconds: int | None = None


RATE_TERMS = ('too many login', 'too many attempts', 'too many requests', 'слишком много попыт', 'превышен лимит', 'часто пытаетесь', 'haddan ziyod', 'juda ko‘p urinish', "juda ko'p urinish", 'ko‘p marta urinish', 'ko\'p marta urinish')
BAD_TERMS = ('неверный пароль', 'неверный логин', 'неверные данные', 'invalid credentials', 'incorrect password', 'invalid password', 'noto‘g‘ri parol', "noto'g'ri parol", 'login yoki parol noto')


class RateLimitDetector:
    @staticmethod
    def classify_ui(messages, *, authenticated=False, login_form=False, authentication_pending=False):
        text = ' '.join(messages).lower()
        rate = any(x in text for x in RATE_TERMS)
        bad = (login_form or authentication_pending) and any(x in text for x in BAD_TERMS)
        if rate:
            return Detection(Outcome.RATE_LIMIT, 'AUTH_RATE_LIMIT_UI')
        if authenticated:
            return Detection(Outcome.SUCCESS, 'AUTHENTICATED_CONTENT')
        if bad:
            return Detection(Outcome.BAD_CREDENTIALS, 'AUTH_REJECTED')
        return Detection(Outcome.UNKNOWN, 'NO_AUTH_PROOF')

    @staticmethod
    def classify_http(status, headers=None, body=None, authentication_request=True):
        headers, body = headers or {}, body or {}
        if authentication_request and (status == 429 or body.get('code') == 'RATE_LIMIT'):
            raw = next((str(v) for k, v in headers.items() if k.lower() == 'retry-after'), '')
            delay = None
            try:
                delay = max(0, int(raw))
            except ValueError:
                try:
                    delay = max(0, int(parsedate_to_datetime(raw).timestamp() - time.time()) + 1)
                except (ValueError, TypeError, OverflowError):
                    pass
            return Detection(Outcome.RATE_LIMIT, 'AUTH_RATE_LIMIT_HTTP', delay)
        if body.get('code') == 'BAD_CREDENTIALS':
            return Detection(Outcome.BAD_CREDENTIALS, 'AUTH_REJECTED')
        if 200 <= status < 300 and body.get('authenticated') is True:
            return Detection(Outcome.SUCCESS, 'AUTHENTICATED_RESPONSE')
        return Detection(Outcome.UNKNOWN, 'UNCLASSIFIED_RESPONSE')


class CooldownScheduler:
    def __init__(self, base=900, multiplier=2, maximum=86400, jitter=.2):
        self.base, self.multiplier, self.maximum, self.jitter = base, multiplier, maximum, jitter

    def next_delay(self, attempt, server_delay=None):
        backoff = min(self.maximum, self.base * self.multiplier ** min(attempt, 20))
        local = min(self.maximum, backoff + random.uniform(0, self.jitter * backoff))
        return max(local, server_delay or 0)


class CircuitBreaker:
    def __init__(self, path, *, cooldown=None, clock=time.time):
        self.path, self.clock = str(path), clock
        self.cooldown = cooldown or CooldownScheduler()
        with self.db() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS breaker(id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL DEFAULT 'IDLE', blocked_at REAL DEFAULT 0, blocked_until REAL DEFAULT 0, attempt INTEGER DEFAULT 0, evidence TEXT DEFAULT '');
            INSERT OR IGNORE INTO breaker(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS recovery_attempts(at REAL NOT NULL);''')
            c.execute("UPDATE breaker SET state='PAUSED_RATE_LIMIT' WHERE state='HALF_OPEN'")

    def db(self):
        c = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        c.row_factory = sqlite3.Row
        return c

    def status(self):
        with self.db() as c:
            row = dict(c.execute('SELECT * FROM breaker WHERE id=1').fetchone())
            budget = c.execute('SELECT count(*) FROM recovery_attempts WHERE at>?', (self.clock()-86400,)).fetchone()[0]
        row['remaining_seconds'] = max(0, int(row['blocked_until']-self.clock()+.999))
        row['recoveries_remaining'] = max(0, 2-budget)
        row['can_resume'] = row['state'] == 'PAUSED_RATE_LIMIT' and row['remaining_seconds'] == 0 and budget < 2
        return row

    def assert_allowed(self):
        if self.status()['state'] in ('PAUSED_RATE_LIMIT', 'PAUSED_REVIEW'):
            raise ValueError('Sikl to‘xtatilgan. Kutish holatini tekshiring')

    def pause_rate(self, detection=None):
        detection = detection or Detection(Outcome.RATE_LIMIT, 'AUTH_RATE_LIMIT_REPORTED')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            n = c.execute('SELECT attempt FROM breaker WHERE id=1').fetchone()[0]
            deadline = self.clock()+self.cooldown.next_delay(n, detection.retry_after_seconds)
            c.execute("UPDATE breaker SET state='PAUSED_RATE_LIMIT',blocked_at=?,blocked_until=max(blocked_until,?),attempt=attempt+1,evidence=? WHERE id=1", (self.clock(), deadline, detection.evidence_code))

    def clear(self):
        """Forget any pause: the user decides when to run again, nothing is locked."""
        with self.db() as c:
            c.execute("UPDATE breaker SET state='IDLE',blocked_at=0,blocked_until=0,attempt=0 WHERE id=1")
            c.execute('DELETE FROM recovery_attempts')

    def pause_review(self):
        with self.db() as c:
            c.execute("UPDATE breaker SET state='PAUSED_REVIEW',evidence='NO_AUTH_PROOF' WHERE id=1 AND state!='PAUSED_RATE_LIMIT'")

    def resume(self):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM breaker WHERE id=1').fetchone()
            if row['state'] == 'PAUSED_REVIEW' and row['blocked_at'] == 0:
                c.execute("UPDATE breaker SET state='IDLE' WHERE id=1")
                return
            count = c.execute('SELECT count(*) FROM recovery_attempts WHERE at>?', (self.clock()-86400,)).fetchone()[0]
            if row['state'] not in ('PAUSED_RATE_LIMIT','PAUSED_REVIEW') or row['blocked_until'] > self.clock() or count >= 2:
                raise ValueError('Kutish muddati tugamagan yoki 24 soatlik qayta urinish chegarasi tugagan')
            c.execute('INSERT INTO recovery_attempts(at) VALUES(?)', (self.clock(),))
            c.execute('DELETE FROM recovery_attempts WHERE at<=?', (self.clock()-86400,))
            c.execute("UPDATE breaker SET state='HALF_OPEN' WHERE id=1")

    def success(self):
        with self.db() as c:
            c.execute("UPDATE breaker SET state='IDLE',blocked_at=0,blocked_until=0,attempt=0,evidence='' WHERE id=1 AND state='HALF_OPEN'")

    def rejected(self):
        # A rejected single recovery probe cannot unlock a previously paused batch.
        if self.status()['state'] == 'HALF_OPEN':
            self.pause_rate(Detection(Outcome.RATE_LIMIT, 'RECOVERY_PROBE_NOT_SUCCESSFUL'))


class SessionStore:
    def __init__(self, path, vault):
        self.path, self.vault = str(path), vault
        with sqlite3.connect(self.path, factory=ClosingConnection) as c:
            c.execute('CREATE TABLE IF NOT EXISTS browser_sessions(account_id TEXT PRIMARY KEY, token BLOB NOT NULL)')

    def load(self, aid):
        with sqlite3.connect(self.path, factory=ClosingConnection) as c:
            r = c.execute('SELECT token FROM browser_sessions WHERE account_id=?', (aid,)).fetchone()
        if not r:
            return None
        try:
            return json.loads(self.vault.decrypt(r[0]))
        except Exception:
            self.delete(aid)
            return None

    def save(self, aid, state):
        with sqlite3.connect(self.path, factory=ClosingConnection) as c:
            c.execute('INSERT OR REPLACE INTO browser_sessions VALUES(?,?)', (aid, self.vault.encrypt(json.dumps(state).encode())))

    def delete(self, aid):
        with sqlite3.connect(self.path, factory=ClosingConnection) as c:
            c.execute('DELETE FROM browser_sessions WHERE account_id=?', (aid,))


def session_evaluation():
    return {'web': {'implemented': True, 'storage': 'Fernet-encrypted per-account storage_state', 'ram': 'one active context', 'login_pressure': 'lower when valid', 'isolation': 'separate context per account'}, 'terms_compatibility': 'Not established; session reuse does not authorize automation'}


class LocalIdentityRotator:
    """Synthetic fixture rotation only; does not touch IP, proxies or Android settings."""
    def __init__(self):
        self.index = 0

    def next_fixture(self, target_url):
        from urllib.parse import urlparse
        p = urlparse(target_url)
        if p.scheme != 'http' or p.hostname not in ('127.0.0.1', 'localhost') or p.port not in (8765, 8877, int(os.environ.get('EMAKTAB_PORT', '8765'))):
            raise ValueError('Identity fixtures are restricted to the loopback simulator')
        if p.port != 8877 and not p.path.startswith('/simulation/'):
            raise ValueError('Only simulator endpoints accept fixture identities')
        self.index = 1-self.index
        return f'lab-device-0{self.index+1}'
