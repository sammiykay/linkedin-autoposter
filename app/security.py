"""Revocable sessions and transactional, persistent login throttling."""
import hashlib
import hmac
import secrets
import time
from app import config, db


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def credential(secret):
    return hmac.new(secret.encode(), config.APP_PASSWORD.encode(), hashlib.sha256).hexdigest()


def authenticated(sid, secret):
    if not sid:
        return False
    with db.conn() as c:
        c.execute('DELETE FROM sessions WHERE expires_at<=? OR credential<>?', (time.time(), credential(secret)))
        return c.execute('SELECT 1 FROM sessions WHERE digest=?', (digest(sid),)).fetchone() is not None


def login(password, secret):
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM login_attempts WHERE at<?', (time.time()-300,))
        if c.execute('SELECT COUNT(*) FROM login_attempts').fetchone()[0] >= 10:
            return None, 429
        if not config.APP_PASSWORD or config.APP_PASSWORD == 'change-me-to-something-long':
            return None, 503
        if not hmac.compare_digest(password.encode(), config.APP_PASSWORD.encode()):
            c.execute('INSERT INTO login_attempts VALUES(?)', (time.time(),))
            return None, 401
        c.execute('DELETE FROM login_attempts')
        sid = secrets.token_urlsafe(32)
        c.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(sid), credential(secret), time.time()+43200))
        db.audit(c, 'session.created')
        return sid, 200


def revoke(sid):
    with db.conn() as c:
        c.execute('DELETE FROM sessions WHERE digest=?', (digest(sid or ''),))
        db.audit(c, 'session.revoked')
