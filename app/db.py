"""SQLite transactions and additive, versioned migrations."""
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from app.config import DB_PATH

FMT = '%Y-%m-%d %H:%M:%S'
PENDING = ('draft', 'scheduled', 'failed', 'missed')
POST_FIELDS = {'text', 'status', 'scheduled_at', 'image_path', 'alt_text', 'attempts', 'post_urn', 'error', 'posted_at', 'next_attempt_at'}


def now_str():
    return datetime.now().strftime(FMT)


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000')
    try:
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def audit(c, action, pid=None, detail='', actor='owner'):
    c.execute('INSERT INTO audit_events(action,post_id,detail,actor,created_at) VALUES(?,?,?,?,?)',
              (action, pid, str(detail)[:1000], actor, now_str()))


def init():
    with conn() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
        c.execute('''CREATE TABLE IF NOT EXISTS posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, image_path TEXT, alt_text TEXT,
            scheduled_at TEXT, status TEXT NOT NULL DEFAULT 'draft', attempts INTEGER NOT NULL DEFAULT 0,
            post_urn TEXT, error TEXT, posted_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_status_sched ON posts(status, scheduled_at)')
        c.execute('CREATE TABLE IF NOT EXISTS worker_state(id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL, state TEXT NOT NULL)')
        if not c.execute('SELECT 1 FROM schema_migrations WHERE version=1').fetchone():
            columns = {r['name'] for r in c.execute('PRAGMA table_info(posts)')}
            if 'next_attempt_at' not in columns:
                c.execute('ALTER TABLE posts ADD COLUMN next_attempt_at TEXT')
            c.execute('CREATE TABLE IF NOT EXISTS publishing_control(id INTEGER PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0)')
            columns = {r['name'] for r in c.execute('PRAGMA table_info(publishing_control)')}
            for name, definition in [('auth_blocked', 'INTEGER NOT NULL DEFAULT 0'), ('auth_reason', "TEXT NOT NULL DEFAULT ''"), ('deploy_paused', 'INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns:
                    c.execute(f'ALTER TABLE publishing_control ADD COLUMN {name} {definition}')
            c.execute('INSERT OR IGNORE INTO publishing_control(id) VALUES(1)')
            for sql in [
                'CREATE TABLE audit_events(id INTEGER PRIMARY KEY, action TEXT NOT NULL, post_id INTEGER, detail TEXT, actor TEXT, created_at TEXT NOT NULL)',
                'CREATE INDEX audit_post ON audit_events(post_id,id)',
                'CREATE TABLE attempts(id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, phase TEXT NOT NULL, outcome TEXT, error TEXT, started_at TEXT NOT NULL, finished_at TEXT)',
                'CREATE TABLE idempotency(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL, created_at REAL NOT NULL)',
                'CREATE TABLE imports(id INTEGER PRIMARY KEY, digest TEXT NOT NULL, response TEXT NOT NULL, created_at REAL NOT NULL)',
                'CREATE INDEX import_digest ON imports(digest,created_at)',
                'CREATE TABLE sessions(digest TEXT PRIMARY KEY, credential TEXT NOT NULL, expires_at REAL NOT NULL)',
                'CREATE TABLE login_attempts(at REAL NOT NULL)',
                'CREATE TABLE operations(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL)',
                'CREATE TABLE incidents(name TEXT PRIMARY KEY, active INTEGER NOT NULL, detail TEXT NOT NULL, changed_at REAL NOT NULL)',
                'CREATE TABLE alert_outbox(id INTEGER PRIMARY KEY, subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT \'pending\', attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL, error TEXT, created_at REAL NOT NULL, sent_at REAL)',
            ]:
                c.execute(sql)
            c.execute('INSERT INTO schema_migrations VALUES(1,?)', (now_str(),))
            audit(c, 'schema.migrated', detail='Reliability schema v1', actor='system')
        if not c.execute('SELECT 1 FROM schema_migrations WHERE version=2').fetchone():
            c.execute('ALTER TABLE alert_outbox ADD COLUMN provider TEXT')
            c.execute('ALTER TABLE alert_outbox ADD COLUMN provider_message_id TEXT')
            c.execute('INSERT INTO schema_migrations VALUES(2,?)', (now_str(),))
            audit(c, 'schema.migrated', detail='Email provider receipts v2', actor='system')


def insert_post(c, values):
    allowed = {k: v for k, v in values.items() if k in POST_FIELDS}
    cur = c.execute(f"INSERT INTO posts({','.join(allowed)}) VALUES({','.join('?' for _ in allowed)})", tuple(allowed.values()))
    audit(c, 'post.created', cur.lastrowid, allowed.get('status', 'draft'))
    return cur.lastrowid


def add_post(text, scheduled_at=None, status='draft', image_path=None, alt_text=None):
    with conn() as c:
        return insert_post(c, dict(text=text, scheduled_at=scheduled_at, status=status, image_path=image_path, alt_text=alt_text))


def get(pid):
    with conn() as c:
        r = c.execute('SELECT * FROM posts WHERE id=?', (pid,)).fetchone()
        return dict(r) if r else None


def all_posts():
    with conn() as c:
        return [dict(r) for r in c.execute('SELECT * FROM posts ORDER BY id DESC')]


def list_posts(page=1, limit=20, status='', search=''):
    where, args = ['1=1'], []
    if status == 'attention':
        where.append("status IN ('failed','missed','uncertain')")
    elif status:
        where.append('status=?'); args.append(status)
    if search:
        where.append('instr(lower(text),lower(?))>0'); args.append(search)
    clause = ' AND '.join(where)
    with conn() as c:
        total = c.execute(f'SELECT COUNT(*) FROM posts WHERE {clause}', args).fetchone()[0]
        rows = c.execute(f'SELECT * FROM posts WHERE {clause} ORDER BY id DESC LIMIT ? OFFSET ?', (*args, limit, (page-1)*limit))
        return dict(items=[dict(r) for r in rows], total=total, page=page, limit=limit)


def update(pid, **fields):
    if not fields or not set(fields) <= POST_FIELDS:
        raise ValueError('Invalid post fields')
    with conn() as c:
        c.execute(f"UPDATE posts SET {','.join(k+'=?' for k in fields)} WHERE id=?", (*fields.values(), pid))
        audit(c, 'post.updated', pid, json.dumps(fields, ensure_ascii=False), 'worker')


def edit_pending(pid, **fields):
    if not fields or not set(fields) <= POST_FIELDS:
        raise ValueError('Invalid post fields')
    with conn() as c:
        count = c.execute(f"UPDATE posts SET {','.join(k+'=?' for k in fields)} WHERE id=? AND status IN ('draft','scheduled','failed','missed')", (*fields.values(), pid)).rowcount
        if count:
            audit(c, 'post.edited', pid, json.dumps(fields, ensure_ascii=False))
        return count == 1


def delete_pending(pid):
    with conn() as c:
        count = c.execute("DELETE FROM posts WHERE id=? AND status IN ('draft','scheduled','failed','missed')", (pid,)).rowcount
        if count:
            audit(c, 'post.deleted', pid)
        return count == 1


def delete(pid):
    return delete_pending(pid)


def controls():
    with conn() as c:
        r = dict(c.execute('SELECT * FROM publishing_control WHERE id=1').fetchone())
        r['paused'] = bool(r['paused']); r['auth_blocked'] = bool(r['auth_blocked']); r['deploy_paused'] = bool(r['deploy_paused'])
        r['can_publish'] = not (r['paused'] or r['auth_blocked'] or r['deploy_paused'])
        return r


def set_control(actor='owner', **fields):
    if not set(fields) <= {'paused', 'auth_blocked', 'auth_reason', 'deploy_paused'}:
        raise ValueError('Invalid publishing control')
    with conn() as c:
        c.execute(f"UPDATE publishing_control SET {','.join(k+'=?' for k in fields)} WHERE id=1", tuple(fields.values()))
        audit(c, 'publishing.control', detail=json.dumps(fields), actor=actor)
    return controls()


def publishing_control(paused=None):
    if paused is not None:
        set_control(paused=int(paused))
    return not controls()['can_publish']


def claim_due(missed_after_hours, limit=100):
    now = datetime.now(); stamp = now.strftime(FMT)
    with conn() as c:
        c.execute('BEGIN IMMEDIATE')
        state = c.execute('SELECT * FROM publishing_control WHERE id=1').fetchone()
        if state['paused'] or state['auth_blocked'] or state['deploy_paused']:
            return []
        cutoff = (now - timedelta(hours=missed_after_hours)).strftime(FMT)
        for r in c.execute("SELECT id FROM posts WHERE status='scheduled' AND scheduled_at<?", (cutoff,)).fetchall():
            c.execute("UPDATE posts SET status='missed',error='Overdue beyond grace window' WHERE id=?", (r['id'],))
            audit(c, 'post.missed', r['id'], actor='worker')
        rows = c.execute("SELECT * FROM posts WHERE status='scheduled' AND scheduled_at<=? AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY scheduled_at LIMIT ?", (stamp, stamp, limit)).fetchall()
        claimed = []
        for row in rows:
            c.execute("UPDATE posts SET status='posting',attempts=attempts+1 WHERE id=?", (row['id'],))
            audit(c, 'post.claimed', row['id'], actor='worker')
            post = dict(row); post['attempts'] += 1; claimed.append(post)
        return claimed


def start_attempt(pid):
    with conn() as c:
        cur = c.execute("INSERT INTO attempts(post_id,phase,started_at) VALUES(?,'preparing',?)", (pid, now_str()))
        audit(c, 'attempt.started', pid, actor='worker')
        return cur.lastrowid


def attempt_phase(aid, phase):
    with conn() as c:
        c.execute('UPDATE attempts SET phase=? WHERE id=?', (phase, aid))
        row = c.execute('SELECT post_id FROM attempts WHERE id=?', (aid,)).fetchone()
        audit(c, 'attempt.phase', row['post_id'], phase, 'worker')


def finish_attempt(aid, outcome, detail='', **fields):
    with conn() as c:
        pid = c.execute('SELECT post_id FROM attempts WHERE id=?', (aid,)).fetchone()[0]
        c.execute('UPDATE attempts SET outcome=?,error=?,finished_at=? WHERE id=?', (outcome, detail[:1000], now_str(), aid))
        c.execute(f"UPDATE posts SET {','.join(k+'=?' for k in fields)} WHERE id=?", (*fields.values(), pid))
        audit(c, 'attempt.'+outcome, pid, detail, 'worker')


def recover_stuck(minutes=15):
    with conn() as c:
        c.execute('BEGIN IMMEDIATE')
        for row in c.execute("SELECT id FROM posts WHERE status='posting'").fetchall():
            # Unknown legacy phase is deliberately conservative.
            c.execute("UPDATE posts SET status='uncertain',error='Worker stopped during publishing. Check LinkedIn before resolving.' WHERE id=?", (row['id'],))
            c.execute("UPDATE attempts SET outcome='uncertain',finished_at=? WHERE post_id=? AND outcome IS NULL", (now_str(), row['id']))
            audit(c, 'post.uncertain', row['id'], 'Worker restart recovery', 'worker')


def heartbeat(state='running'):
    with conn() as c:
        c.execute('INSERT OR REPLACE INTO worker_state VALUES(1,?,?)', (time.time(), state))


def worker_health():
    from app import config
    with conn() as c:
        row = c.execute('SELECT * FROM worker_state WHERE id=1').fetchone()
    age = max(0, time.time()-row['heartbeat']) if row else None
    return dict(online=bool(row and row['state']=='running' and age < max(300, config.CHECK_EVERY_SECONDS*3)), seconds_ago=round(age) if age is not None else None)


def operation(kind, status, detail):
    with conn() as c:
        c.execute('INSERT INTO operations(kind,status,detail,created_at) VALUES(?,?,?,?)', (kind,status,str(detail)[:2000],time.time()))
