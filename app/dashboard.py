"""Authenticated Postdesk API and local HTML dashboard."""
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request, session, send_from_directory
from werkzeug.exceptions import HTTPException
from app import config, db, images, linkedin, security


class Conflict(Exception):
    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


def create_app(test_config=None):
    app = Flask(__name__)
    key_path = config.DATA_DIR / 'session.key'
    if not key_path.exists():
        try:
            with open(key_path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    secret = key_path.read_text().strip()
    if len(secret) < 64:
        raise RuntimeError('Session signing key is invalid')
    app.config.update(SECRET_KEY=secret, MAX_CONTENT_LENGTH=20*1024*1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    if test_config:
        app.config.update(test_config)
    db.init()

    @app.before_request
    def protect():
        if request.path.startswith(('/api/', '/media/')):
            if request.path not in ('/api/session', '/api/login') and not security.authenticated(session.get('sid'), secret):
                return jsonify(error='Sign in to continue.'), 401
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                token = session.get('csrf', '')
                if not token or not hmac.compare_digest(token.encode(), request.headers.get('X-CSRF-Token', '').encode()):
                    return jsonify(error='Session expired. Refresh the page.'), 403

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
            'X-Frame-Options':'DENY', 'Referrer-Policy':'same-origin',
            'Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Conflict)
    def conflict(error):
        return jsonify(error=str(error), **error.extra), 409

    @app.errorhandler(ValueError)
    @app.errorhandler(images.ImageError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.exception('Dashboard request failed')
        return jsonify(error='An internal error occurred. Check dashboard service logs.'), 500

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/healthz')
    def health():
        with db.conn() as c:
            c.execute('SELECT 1')
        return jsonify(status='ok')

    @app.get('/api/session')
    def current_session():
        session.setdefault('csrf', secrets.token_hex(32))
        return jsonify(authenticated=security.authenticated(session.get('sid'), secret), csrf=session['csrf'])

    @app.post('/api/login')
    def login():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Supply a password')
        sid, status = security.login(str(data.get('password', '')), secret)
        if status != 200:
            messages = {401:'Incorrect password.', 429:'Too many attempts. Try again in five minutes.', 503:'Set a strong APP_PASSWORD in .env before signing in.'}
            return jsonify(error=messages[status]), status
        security.revoke(session.get('sid'))
        session.clear(); session.update(sid=sid, csrf=secrets.token_hex(32)); session.permanent=True
        return jsonify(csrf=session['csrf'])

    @app.post('/api/logout')
    def logout():
        security.revoke(session.get('sid')); session.clear()
        return jsonify(ok=True)

    @app.get('/api/overview')
    def overview():
        with db.conn() as c:
            counts = {r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM posts GROUP BY status')}
            upcoming = [dict(r) for r in c.execute("SELECT * FROM posts WHERE status='scheduled' ORDER BY scheduled_at LIMIT 5")]
            events = [dict(r) for r in c.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT 6')]
        return jsonify(counts=counts, upcoming=upcoming, activity=events, controls=db.controls(), worker=db.worker_health(),
                       token_days=linkedin.token_days_left(), server_time=db.now_str(),
                       timezone=datetime.now().astimezone().tzname(), poll_seconds=config.CHECK_EVERY_SECONDS,
                       grace_hours=config.MISSED_AFTER_HOURS)

    def pagination():
        try:
            page = int(request.args.get('page', 1)); limit = int(request.args.get('limit', 20))
        except ValueError:
            raise ValueError('Invalid pagination')
        if page < 1 or not 1 <= limit <= 100:
            raise ValueError('Page must be positive and limit between 1 and 100')
        return page, limit

    @app.get('/api/posts')
    def posts():
        page, limit = pagination()
        status = request.args.get('status', '')
        if status not in ('', 'draft', 'scheduled', 'posting', 'posted', 'failed', 'missed', 'uncertain', 'attention'):
            raise ValueError('Unknown status')
        return jsonify(db.list_posts(page, limit, status, request.args.get('search', '')[:300]))

    @app.get('/api/posts/<int:pid>')
    def post(pid):
        p = db.get(pid)
        if not p:
            return jsonify(error='Post not found'), 404
        return jsonify(p)

    @app.get('/api/calendar')
    def calendar():
        try:
            start = datetime.fromisoformat(request.args.get('start', ''))
            end = datetime.fromisoformat(request.args.get('end', ''))
            if start.tzinfo or end.tzinfo or not 0 < (end-start).total_seconds() <= 62*86400:
                raise ValueError()
        except ValueError:
            raise ValueError('Supply a date range of at most 62 days')
        with db.conn() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM posts WHERE scheduled_at>=? AND scheduled_at<? ORDER BY scheduled_at LIMIT 1001', (start.strftime(db.FMT),end.strftime(db.FMT)))]
        return jsonify(items=rows[:1000], truncated=len(rows)>1000)

    @app.get('/api/activity')
    def activity():
        page, limit = pagination()
        with db.conn() as c:
            total = c.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]
            rows = [dict(r) for r in c.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT ? OFFSET ?', (limit,(page-1)*limit))]
        return jsonify(items=rows, total=total, page=page, limit=limit)

    @app.get('/api/system')
    def system():
        from app import alerts
        with db.conn() as c:
            operations=[dict(r) for r in c.execute('SELECT * FROM operations ORDER BY id DESC LIMIT 20')]
            incidents=[dict(r) for r in c.execute('SELECT * FROM incidents ORDER BY name')]
            mail=[dict(r) for r in c.execute('SELECT id,subject,status,attempts,next_at,error,created_at,sent_at,provider,provider_message_id FROM alert_outbox ORDER BY id DESC LIMIT 10')]
        return jsonify(operations=operations, incidents=incidents, alerts=mail, email_configured=alerts.configured(), email_provider=config.EMAIL_PROVIDER, email_missing=alerts.missing_configuration(),
                       backup_scope='Local only — loss of this computer can also destroy backups.')

    @app.post('/api/publishing')
    def publishing():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('paused'), bool):
            raise ValueError('paused must be true or false')
        return jsonify(controls=db.set_control(paused=int(data['paused'])))

    @app.post('/api/connection/validate')
    def validate_connection():
        try:
            linkedin.validate_connection()
        except linkedin.LinkedInError as e:
            db.set_control(actor='system', auth_blocked=1, auth_reason=str(e)[:500])
            return jsonify(error=str(e)), 422
        return jsonify(controls=db.set_control(auth_blocked=0, auth_reason=''))

    @app.get('/media/<path:name>')
    def media(name):
        path=images.resolve(name)
        return send_from_directory(path.parent,path.name)

    def fields(data, existing=None, allow_immediate=True):
        text=str(data.get('text','')).strip()
        if not text or len(text)>config.MAX_POST_CHARS:
            raise ValueError('Post text must contain 1–3000 characters.')
        status=data.get('status','draft'); when=None
        if status not in ('draft','scheduled'):
            raise ValueError('Choose draft or scheduled.')
        if status=='scheduled':
            if allow_immediate and data.get('immediate')=='true':
                when=db.now_str()
            else:
                try:
                    parsed=datetime.fromisoformat(str(data.get('scheduled_at','')))
                    if parsed.tzinfo or parsed<=datetime.now():
                        raise ValueError()
                    when=parsed.strftime(db.FMT)
                except ValueError:
                    raise ValueError('Choose a future date and time in the server timezone.')
        image_path=existing.get('image_path') if existing else None
        upload=request.files.get('image')
        if upload:
            image_path=images.save_upload(upload.filename,upload.read())
        if data.get('remove_image')=='true':
            image_path=None
        return dict(text=text,status=status,scheduled_at=when,image_path=image_path,alt_text=str(data.get('alt_text',''))[:4000])

    def request_identity():
        key=request.headers.get('Idempotency-Key','')
        if not 8<=len(key)<=128 or not key.isascii():
            raise ValueError('Supply an Idempotency-Key of 8–128 ASCII characters')
        payload={'path':request.path,'form':sorted(request.form.items(multi=True)), 'files':[]}
        for name, f in request.files.items():
            payload['files'].append((name,f.filename,hashlib.sha256(f.read()).hexdigest())); f.seek(0)
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        return key,fingerprint

    def replay(c,key,fingerprint):
        row=c.execute('SELECT * FROM idempotency WHERE key=?',(key,)).fetchone()
        if row:
            if row['fingerprint']!=fingerprint:
                raise Conflict('This request key was already used with different content.')
            return json.loads(row['response'])

    def remember(c,key,fingerprint,result):
        c.execute('INSERT INTO idempotency VALUES(?,?,?,?)',(key,fingerprint,json.dumps(result),time.time()))

    @app.post('/api/posts')
    def add():
        key,fingerprint=request_identity()
        # Replay before validating a date that may now be in the past.
        with db.conn() as c:
            c.execute('BEGIN IMMEDIATE')
            prior=replay(c,key,fingerprint)
            if prior is not None:
                return jsonify(prior),200
            values=fields(request.form)
            result={'id':db.insert_post(c,values)}
            remember(c,key,fingerprint,result)
        return jsonify(result),201

    @app.put('/api/posts/<int:pid>')
    def edit(pid):
        existing=db.get(pid)
        if not existing:
            return jsonify(error='Post not found.'),404
        values=fields(request.form,existing); values.update(error=None,attempts=0,next_attempt_at=None)
        if not db.edit_pending(pid,**values):
            raise Conflict('This post is publishing, published, or uncertain. Refresh the queue.')
        return jsonify(ok=True)

    @app.delete('/api/posts/<int:pid>')
    def delete(pid):
        if not db.delete_pending(pid):
            raise Conflict('Only pending posts can be deleted.')
        return jsonify(ok=True)

    @app.post('/api/posts/<int:pid>/resolve')
    def resolve(pid):
        data=request.get_json(silent=True)
        if not isinstance(data,dict):
            raise ValueError('Supply a recovery decision')
        if data.get('checked_linkedin') is not True or data.get('outcome') not in ('posted','draft'):
            raise ValueError('Confirm that you checked LinkedIn, then choose published or draft.')
        urn=str(data.get('post_urn','')).strip()
        if urn and not urn.startswith(('urn:li:share:','urn:li:ugcPost:')):
            raise ValueError('Use a LinkedIn post URN or leave it blank.')
        with db.conn() as c:
            c.execute('BEGIN IMMEDIATE')
            count=c.execute("UPDATE posts SET status=?,post_urn=?,error=NULL,attempts=0,next_attempt_at=NULL,posted_at=?,scheduled_at=CASE WHEN ?='draft' THEN NULL ELSE scheduled_at END WHERE id=? AND status='uncertain'",(data['outcome'],urn or None,db.now_str() if data['outcome']=='posted' else None,data['outcome'],pid)).rowcount
            if not count:
                raise Conflict('Only uncertain posts can be resolved.')
            db.audit(c,'post.resolved',pid,data['outcome']+'; owner confirmed LinkedIn review')
        return jsonify(ok=True)

    @app.post('/api/import')
    def bulk_import():
        key,fingerprint=request_identity()
        with db.conn() as c:
            prior=replay(c,key,fingerprint)
            if prior is not None:
                return jsonify(prior)
        upload=request.files.get('file')
        if not upload or Path(upload.filename).suffix.lower() not in ('.csv','.xlsx'):
            raise ValueError('Upload a CSV or XLSX file.')
        raw=upload.read(); digest=hashlib.sha256(raw).hexdigest()
        # Check duplicates before date validation: old scheduled times may have passed.
        with db.conn() as c:
            old=c.execute('SELECT response FROM imports WHERE digest=? AND created_at>? ORDER BY id DESC LIMIT 1',(digest,time.time()-86400)).fetchone()
        if old and request.form.get('confirm_duplicate')!='true':
            raise Conflict('This file was imported in the last 24 hours.',duplicate_import=True,previous=json.loads(old['response']))
        try:
            if upload.filename.lower().endswith('.csv'):
                frame=pd.read_csv(io.BytesIO(raw),nrows=501)
            else:
                import zipfile
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    if sum(i.file_size for i in archive.infolist())>50*1024*1024:
                        raise ValueError('Spreadsheet expands beyond the 50 MB safety limit.')
                frame=pd.read_excel(io.BytesIO(raw),nrows=501)
        except Exception:
            raise ValueError('Could not read the spreadsheet within supported limits.')
        frame.columns=[str(c).strip().lower() for c in frame.columns]
        if frame.columns.duplicated().any() or 'text' not in frame.columns or not 0<len(frame)<=500:
            raise ValueError('Use unique columns including text, and between 1 and 500 rows.')
        rows=[]
        for n,row in frame.fillna('').iterrows():
            if str(row.get('image','')).strip():
                raise ValueError(f'Row {n+2}: attach images through the editor after importing.')
            try:
                rows.append(fields(dict(text=row['text'],scheduled_at=str(row.get('scheduled_at','')),status='scheduled' if str(row.get('scheduled_at','')).strip() else 'draft',alt_text=row.get('alt_text','')),allow_immediate=False))
            except ValueError as e:
                raise ValueError(f'Row {n+2}: {e}')
        with db.conn() as c:
            c.execute('BEGIN IMMEDIATE')
            prior=replay(c,key,fingerprint)
            if prior is not None:
                return jsonify(prior)
            old=c.execute('SELECT response FROM imports WHERE digest=? AND created_at>? ORDER BY id DESC LIMIT 1',(digest,time.time()-86400)).fetchone()
            if old and request.form.get('confirm_duplicate')!='true':
                raise Conflict('This file was already imported.',duplicate_import=True,previous=json.loads(old['response']))
            ids=[db.insert_post(c,row) for row in rows]
            result={'count':len(ids),'ids':ids}
            c.execute('INSERT INTO imports(digest,response,created_at) VALUES(?,?,?)',(digest,json.dumps(result),time.time()))
            remember(c,key,fingerprint,result); db.audit(c,'posts.imported',detail=f'{len(ids)} posts')
        return jsonify(result),201

    return app


def main():
    from waitress import serve
    serve(create_app(),host='127.0.0.1',port=config.DASHBOARD_PORT,threads=4,max_request_body_size=20*1024*1024,max_request_header_size=32768)


if __name__=='__main__':
    main()
