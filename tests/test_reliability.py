import hashlib
import io
import json
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from app import config,db,linkedin,worker,alerts,monitor,backup,security,deployment
from app.dashboard import create_app


@pytest.fixture
def app():
    return create_app({'TESTING':True})


@pytest.fixture
def client(app):
    return app.test_client()


def sign_in(client):
    csrf=client.get('/api/session').json['csrf']
    response=client.post('/api/login',json={'password':config.APP_PASSWORD},headers={'X-CSRF-Token':csrf})
    assert response.status_code==200
    return {'X-CSRF-Token':response.json['csrf'],'Idempotency-Key':uuid.uuid4().hex}


def test_creation_replay_conflict_and_concurrency(app,client):
    headers=sign_in(client)
    first=client.post('/api/posts',data={'text':'Same submission'},headers=headers)
    assert first.status_code==201
    assert client.post('/api/posts',data={'text':'Same submission'},headers=headers).json==first.json
    assert client.post('/api/posts',data={'text':'Different submission'},headers=headers).status_code==409
    assert len(db.all_posts())==1
    # Same authenticated cookie, independent connections and concurrent requests.
    cookie=client.get_cookie('session').value
    headers['Idempotency-Key']=uuid.uuid4().hex
    def submit(_):
        with app.test_client() as parallel:
            parallel.set_cookie('session',cookie)
            return parallel.post('/api/posts',data={'text':'Concurrent'},headers=headers).json['id']
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert len(set(executor.map(submit,range(4))))==1
    assert len(db.all_posts())==2


def test_import_replay_duplicate_confirmation(client):
    headers=sign_in(client)
    def submit(confirm=False):
        data={'file':(io.BytesIO(b'text\nHello\n'),'posts.csv')}
        if confirm:data['confirm_duplicate']='true'
        return client.post('/api/import',data=data,headers=headers)
    first=submit();assert first.status_code==201
    assert submit().json==first.json
    headers['Idempotency-Key']=uuid.uuid4().hex
    duplicate=submit();assert duplicate.status_code==409
    assert duplicate.json['previous']==first.json
    headers['Idempotency-Key']=uuid.uuid4().hex
    assert submit(True).status_code==201
    assert len(db.all_posts())==2


def test_sessions_revoked_logout_password_and_app_restart(client):
    headers=sign_in(client)
    cookie=client.get_cookie('session').value
    # Server restart keeps valid sessions, but logout revokes replayed signed cookies.
    restarted=create_app({'TESTING':True}).test_client();restarted.set_cookie('session',cookie)
    assert restarted.get('/api/overview').status_code==200
    client.post('/api/logout',headers=headers)
    assert restarted.get('/api/overview').status_code==401
    sign_in(client);cookie=client.get_cookie('session').value
    config.APP_PASSWORD='a-new-workspace-password'
    restarted.set_cookie('session',cookie)
    assert restarted.get('/api/overview').status_code==401
    with db.conn() as c:
        assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0


def test_throttling_survives_application_restart(client):
    csrf=client.get('/api/session').json['csrf']
    for _ in range(10):
        assert client.post('/api/login',json={'password':'wrong'},headers={'X-CSRF-Token':csrf}).status_code==401
    other=create_app({'TESTING':True}).test_client()
    csrf=other.get('/api/session').json['csrf']
    assert other.post('/api/login',json={'password':config.APP_PASSWORD},headers={'X-CSRF-Token':csrf}).status_code==429


def test_pagination_calendar_and_audit(client):
    headers=sign_in(client)
    future=(datetime.now()+timedelta(days=2)).strftime(db.FMT)
    for n in range(25):db.add_post(f'Post {n}',future,'scheduled')
    page=client.get('/api/posts?page=2&limit=20').json
    assert len(page['items'])==5 and page['total']==25
    assert client.get('/api/posts?limit=1000').status_code==400
    start=datetime.now().strftime('%Y-%m-%d');end=(datetime.now()+timedelta(days=5)).strftime('%Y-%m-%d')
    assert len(client.get(f'/api/calendar?start={start}&end={end}').json['items'])==25
    assert client.get('/api/calendar?start=2020-01-01&end=2026-12-01').status_code==400
    pid=page['items'][0]['id']
    assert client.put(f'/api/posts/{pid}',data={'text':'Edited'},headers=headers).status_code==200
    events=client.get('/api/activity').json['items']
    assert any(e['action']=='post.edited' and e['post_id']==pid for e in events)


def test_authorization_pause_preserves_remaining_and_manual_pause(monkeypatch,client):
    ids=[db.add_post('Post',db.now_str(),'scheduled') for _ in range(2)]
    post=db.claim_due(24,1)[0]
    def reject(*args,**kwargs):raise linkedin.TokenError('Token rejected')
    monkeypatch.setattr(linkedin,'publish',reject)
    worker.process_one(post)
    assert all(db.get(pid)['status']=='scheduled' for pid in ids)
    assert db.controls()['auth_blocked'] and not db.claim_due(24)
    db.set_control(paused=1)
    monkeypatch.setattr(linkedin,'validate_connection',lambda: 'urn:li:person:test')
    headers=sign_in(client)
    assert client.post('/api/connection/validate',headers=headers).status_code==200
    state=db.controls();assert state['paused'] and not state['auth_blocked']
    db.init();assert db.controls()['paused']
    client.post('/api/publishing',json={'paused':False},headers=headers)
    assert len(db.claim_due(24))==2


def test_missing_expired_token_blocks_before_claim():
    pid=db.add_post('Due',db.now_str(),'scheduled')
    assert not worker.authorization_ready()
    assert db.get(pid)['status']=='scheduled' and db.controls()['auth_blocked']
    config.TOKEN_PATH.write_text(json.dumps(dict(access_token='x',issued_at=1,expires_in=1)))
    assert not worker.authorization_ready()


def test_uncertain_publication_requires_acknowledged_resolution(monkeypatch,client):
    pid=db.add_post('Perhaps live',db.now_str(),'scheduled');post=db.claim_due(24,1)[0]
    def interrupted(*args,**kwargs):
        kwargs['before_send']()
        raise requests.Timeout('response lost')
    monkeypatch.setattr(linkedin,'publish',interrupted)
    worker.process_one(post)
    assert db.get(pid)['status']=='uncertain'
    with db.conn() as c:
        attempt=c.execute('SELECT * FROM attempts').fetchone()
        assert attempt['phase']=='sending' and attempt['outcome']=='uncertain'
    headers=sign_in(client)
    assert client.put(f'/api/posts/{pid}',data={'text':'Unsafe retry'},headers=headers).status_code==409
    assert client.post(f'/api/posts/{pid}/resolve',json={'outcome':'draft'},headers=headers).status_code==400
    assert client.post(f'/api/posts/{pid}/resolve',json={'outcome':'draft','checked_linkedin':True},headers=headers).status_code==200
    assert db.get(pid)['status']=='draft'


def test_retry_after_preserves_original_schedule(monkeypatch):
    pid=db.add_post('Retry',db.now_str(),'scheduled');post=db.claim_due(24,1)[0]
    def rejected(*args,**kwargs):raise linkedin.LinkedInError('Rate limited',retryable=True,retry_after=7200)
    monkeypatch.setattr(linkedin,'publish',rejected)
    worker.process_one(post)
    row=db.get(pid)
    assert row['scheduled_at']==post['scheduled_at']
    assert datetime.strptime(row['next_attempt_at'],db.FMT)>=datetime.now()+timedelta(seconds=7198)
    assert db.claim_due(24)==[]
    response=Mock(status_code=503,headers={'Retry-After':'120'})
    with pytest.raises(linkedin.LinkedInError) as caught:linkedin._check(response,'create post')
    assert caught.value.uncertain and not caught.value.retryable
    assert linkedin.retry_after(Mock(headers={'Retry-After':'garbage'})) is None


def test_backup_integrity_restore_and_failure(monkeypatch,tmp_path):
    from PIL import Image
    image=config.MEDIA_DIR/'test.png';Image.new('RGB',(4,4),'blue').save(image)
    pid=db.add_post('Restored',image_path='test.png')
    snapshot=backup.create();assert backup.verify(snapshot)['format']==1
    restored=backup.restore(snapshot,tmp_path/'restored')
    with sqlite3.connect(restored/'posts.db') as c:
        assert c.execute('SELECT text FROM posts WHERE id=?',(pid,)).fetchone()[0]=='Restored'
        assert c.execute('SELECT paused FROM publishing_control').fetchone()[0]==1
    assert (restored/'media/test.png').read_bytes()==image.read_bytes()
    (snapshot/'media.tgz').write_bytes(b'corrupt')
    with pytest.raises(ValueError):backup.verify(snapshot)
    image.unlink()
    with pytest.raises(ValueError):backup.create()
    with db.conn() as c:
        assert c.execute("SELECT status FROM operations WHERE kind='backup' ORDER BY id DESC").fetchone()[0]=='failed'


def test_monitor_alert_retry_dedup_and_recovery(monkeypatch):
    monkeypatch.setattr(monitor,'checks',lambda:{'worker':'Worker stopped'})
    config.SMTP_HOST='mail.example';config.ALERT_TO='owner@example.test'
    def unavailable(*args,**kwargs):raise OSError('offline')
    monkeypatch.setattr(alerts,'deliver',unavailable)
    monitor.run();monitor.run()
    with db.conn() as c:
        rows=c.execute('SELECT * FROM alert_outbox').fetchall()
        assert len(rows)==1 and rows[0]['status']=='pending' and rows[0]['attempts']==1
        c.execute('UPDATE alert_outbox SET next_at=0')
    sent=[];monkeypatch.setattr(alerts,'deliver',lambda *args,**kwargs:sent.append(args))
    monitor.run();assert len(sent)==1
    monkeypatch.setattr(monitor,'checks',lambda:{})
    monitor.run();assert len(sent)==2 and sent[-1][0].startswith('Recovered')


def test_monitor_detects_worker_backup_disk_and_overdue(monkeypatch):
    monkeypatch.setattr(monitor.shutil,'disk_usage',lambda p:Mock(free=0))
    db.add_post('Overdue',(datetime.now()-timedelta(hours=1)).strftime(db.FMT),'scheduled')
    issues=monitor.checks()
    assert {'worker','backup','disk','authorization','overdue'}<=set(issues)


def test_migrate_legacy_queue_and_recover_without_retries(tmp_path,monkeypatch):
    legacy=tmp_path/'legacy.db'
    with sqlite3.connect(legacy) as c:
        c.execute('''CREATE TABLE posts(id INTEGER PRIMARY KEY,text TEXT NOT NULL,image_path TEXT,alt_text TEXT,scheduled_at TEXT,status TEXT,attempts INTEGER DEFAULT 0,post_urn TEXT,error TEXT,posted_at TEXT,created_at TEXT)''')
        c.execute("INSERT INTO posts(id,text,status,error) VALUES(1,'Legacy failed','failed','Worker crashed mid-post. Check LinkedIn before retrying.')")
        c.execute('CREATE TABLE publishing_control(id INTEGER PRIMARY KEY,paused INTEGER DEFAULT 0)')
        c.execute('INSERT INTO publishing_control VALUES(1,1)')
    monkeypatch.setattr(db,'DB_PATH',legacy)
    db.init();db.init()
    assert db.get(1)['status']=='failed' and 'Check LinkedIn' in db.get(1)['error']
    assert db.controls()['paused']
    with db.conn() as c:assert [r[0] for r in c.execute('SELECT version FROM schema_migrations ORDER BY version')]==[1,2,3]


def test_failed_deployment_restores_previous_release_paused(monkeypatch,tmp_path):
    root=tmp_path/'installation';root.mkdir();old=root/'old';new=root/'new';old.mkdir();new.mkdir()
    deployment.switch(root/'current',old)
    monkeypatch.setattr(deployment.config,'DB_PATH',db.DB_PATH)
    monkeypatch.setattr(deployment.subprocess,'run',lambda *args,**kwargs:db.init())
    actions=[]
    def unhealthy():raise RuntimeError('Readiness failed')
    with pytest.raises(RuntimeError):deployment.activate(root,new,manager=actions.append,probe=unhealthy)
    assert (root/'current').resolve()==old
    assert db.controls()['paused'] and db.controls()['deploy_paused']
    assert actions==['stop','start','stop','start']
    assert list((root/'deployment-backups').glob('*/posts.db'))


def test_successful_deployment_restores_pause_and_keeps_previous(monkeypatch,tmp_path):
    root=tmp_path/'installation';root.mkdir();old=root/'old';new=root/'new';old.mkdir();new.mkdir()
    deployment.switch(root/'current',old)
    monkeypatch.setattr(deployment.config,'DB_PATH',db.DB_PATH)
    monkeypatch.setattr(deployment.subprocess,'run',lambda *args,**kwargs:db.init())
    state=deployment.activate(root,new,manager=lambda action:None,probe=lambda:None)
    assert (root/'current').resolve()==new and (root/'previous').resolve()==old
    assert db.controls()['can_publish'] and state['backup']


def test_rollback_success_stays_paused_and_retains_new_release(monkeypatch,tmp_path):
    root=tmp_path/'installation';root.mkdir();old=root/'old';new=root/'new';old.mkdir();new.mkdir()
    deployment.switch(root/'current',new);deployment.switch(root/'previous',old)
    monkeypatch.setattr(deployment.config,'DB_PATH',db.DB_PATH)
    deployment.rollback(root,manager=lambda action:None,probe=lambda:None)
    assert (root/'current').resolve()==old and (root/'previous').resolve()==new
    assert db.controls()['paused'] and db.controls()['deploy_paused']


def test_malformed_mutations_and_csrf(client):
    headers=sign_in(client)
    assert client.post('/api/publishing',json=['bad'],headers=headers).status_code==400
    assert client.post('/api/posts/1/resolve',json=['bad'],headers=headers).status_code==400
    assert client.post('/api/connection/validate').status_code==403
    assert client.post('/api/publishing',json={'paused':True}).status_code==403
    assert client.post('/api/posts',data={'text':'Missing request key'},headers={'X-CSRF-Token':headers['X-CSRF-Token']}).status_code==400


def test_sending_lease_recovers_after_monitor_crash(monkeypatch):
    config.SMTP_HOST='mail.example';config.ALERT_TO='owner@example.test'
    alerts.send('Incident','Needs attention')
    with db.conn() as c:
        c.execute("UPDATE alert_outbox SET status='sending',next_at=?",(time.time()+600,))
    sent=[];monkeypatch.setattr(alerts,'deliver',lambda *args,**kwargs:sent.append(args))
    alerts.flush();assert not sent
    with db.conn() as c:c.execute('UPDATE alert_outbox SET next_at=0')
    alerts.flush();assert len(sent)==1


def test_successful_publish_missing_identifier_is_uncertain(monkeypatch):
    monkeypatch.setattr(linkedin,'load_token_data',lambda:{'access_token':'test'})
    monkeypatch.setattr(linkedin,'get_author_urn',lambda token:'urn:li:person:test')
    monkeypatch.setattr(linkedin.requests,'post',lambda *args,**kwargs:Mock(status_code=201,headers={}))
    phases=[]
    with pytest.raises(linkedin.LinkedInError) as caught:
        linkedin.publish('A post',before_send=lambda:phases.append('sending'))
    assert phases==['sending'] and caught.value.uncertain


def test_backup_retention_only_removes_verified_completed_snapshots(tmp_path):
    old=backup.create()
    manifest=json.loads((old/'manifest.json').read_text());manifest['created_at']=time.time()-15*86400
    (old/'manifest.json').write_text(json.dumps(manifest))
    partial=config.BACKUP_DIR/'.partial-unfinished';partial.mkdir();(partial/'posts.db').write_bytes(b'incomplete')
    recent=backup.create()
    assert recent.exists() and not old.exists() and partial.exists()


def test_real_worker_process_restart_preserves_controls_and_queue(tmp_path):
    import os
    import subprocess
    import sys
    pid=db.add_post('Future post',(datetime.now()+timedelta(days=1)).strftime(db.FMT),'scheduled')
    db.set_control(paused=1)
    env=os.environ.copy()
    env.update(APP_DATA_DIR=str(config.DATA_DIR),APP_MEDIA_DIR=str(config.MEDIA_DIR),APP_LOG_DIR=str(config.LOG_DIR),
               APP_BACKUP_DIR=str(config.BACKUP_DIR),DB_PATH=str(db.DB_PATH),TOKEN_PATH=str(config.TOKEN_PATH),
               CHECK_EVERY_SECONDS='1')
    for _ in range(2):
        started=time.time()
        process=subprocess.Popen([sys.executable,'-m','app.worker'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                with db.conn() as c:
                    row=c.execute('SELECT * FROM worker_state WHERE id=1').fetchone()
                # A heartbeat precedes the first authorization check; wait for both states.
                if row and row['heartbeat']>=started and row['state']=='running' and db.controls()['auth_blocked']:break
                if process.poll() is not None:pytest.fail('Isolated worker exited before heartbeat')
                time.sleep(.05)
            else:pytest.fail('Worker did not report a fresh heartbeat')
            assert db.controls()['paused'] and db.controls()['auth_blocked']
            assert db.get(pid)['status']=='scheduled' and db.get(pid)['attempts']==0
        finally:
            process.terminate();process.wait(timeout=10)
        assert not db.worker_health()['online']
