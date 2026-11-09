"""Release switching, pre-migration snapshots, readiness and fail-closed rollback.

Invoked only by the system installer. Tests inject the service manager and readiness probe.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path
from app import config

SERVICES=['autoposter-worker','autoposter-dashboard']


def switch(link,target):
    link=Path(link);temporary=link.with_name(link.name+'.next')
    temporary.unlink(missing_ok=True);temporary.symlink_to(Path(target).resolve(),target_is_directory=True)
    os.replace(temporary,link)


def raw_pause():
    with sqlite3.connect(config.DB_PATH,timeout=30) as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS publishing_control(id INTEGER PRIMARY KEY,paused INTEGER NOT NULL DEFAULT 0)')
        c.execute('INSERT OR IGNORE INTO publishing_control(id) VALUES(1)')
        previous=bool(c.execute('SELECT paused FROM publishing_control WHERE id=1').fetchone()[0])
        c.execute('UPDATE publishing_control SET paused=1 WHERE id=1')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='audit_events'").fetchone():
            from app import db
            db.audit(c,'publishing.control',detail='Deployment paused new claims',actor='deploy')
    return previous


def hold():
    raw_pause()
    with sqlite3.connect(config.DB_PATH) as c:
        columns={r[1] for r in c.execute('PRAGMA table_info(publishing_control)')}
        if 'deploy_paused' in columns:
            c.execute('UPDATE publishing_control SET deploy_paused=1 WHERE id=1')


def snapshot(root):
    folder=Path(root)/'deployment-backups'/(time.strftime('%Y%m%d-%H%M%S')+'-'+os.urandom(3).hex())
    folder.mkdir(parents=True,exist_ok=False,mode=0o700)
    with sqlite3.connect(config.DB_PATH) as source,sqlite3.connect(folder/'posts.db') as target:
        source.backup(target)
    with tarfile.open(folder/'media.tgz','w:gz') as archive:
        archive.add(config.MEDIA_DIR,arcname='media')
    for path in folder.iterdir():
        path.chmod(0o600)
    return folder


def service(action):
    if action == 'start' and os.geteuid() == 0 and os.getenv('SUDO_USER'):
        import pwd
        owner = pwd.getpwnam(os.environ['SUDO_USER'])
        # SQLite sidecars and first-install databases must be writable before readiness.
        for folder in (config.DATA_DIR, config.LOG_DIR, config.BACKUP_DIR):
            if folder.exists():
                os.chown(folder, owner.pw_uid, owner.pw_gid)
                for path in folder.rglob('*'):
                    if not path.is_symlink():
                        os.chown(path, owner.pw_uid, owner.pw_gid)
        for path in (config.DB_PATH, Path(str(config.DB_PATH)+'-wal'), Path(str(config.DB_PATH)+'-shm')):
            if path.exists() and not path.is_symlink():
                os.chown(path, owner.pw_uid, owner.pw_gid)
    subprocess.run(['systemctl',action,*SERVICES],check=True)


def ready():
    import urllib.request
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{config.DASHBOARD_PORT}/healthz',timeout=2) as response:
                if json.load(response).get('status')!='ok':
                    raise ValueError('Invalid readiness response')
            with sqlite3.connect(config.DB_PATH) as c:
                row=c.execute('SELECT heartbeat,state FROM worker_state WHERE id=1').fetchone()
            if row and row[1]=='running' and time.time()-row[0]<15:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('Dashboard or worker readiness failed')


def activate(root,release,manager=service,probe=ready):
    root=Path(root);release=Path(release);link=root/'current'
    previous=link.resolve() if link.is_symlink() else None
    paused=raw_pause()
    backup=None
    try:
        # systemd waits for the current publication; no new claims after raw_pause.
        manager('stop')
        backup=snapshot(root)
        env=os.environ.copy();env.update(runtime_environment(root))
        subprocess.run([str(release/'venv/bin/python'),'-m','app.deploy_ops','migrate'],cwd=release,env=env,check=True)
        hold()
        with sqlite3.connect(config.DB_PATH) as c:
            c.execute('UPDATE worker_state SET heartbeat=0,state=\'stopped\'')
        switch(link,release)
        manager('start');probe()
        with sqlite3.connect(config.DB_PATH) as c:
            c.execute('UPDATE publishing_control SET paused=?,deploy_paused=0 WHERE id=1',(int(paused),))
            from app import db
            db.audit(c,'deployment.activated',detail=release.name,actor='deploy')
        if previous:
            switch(root/'previous',previous)
        state=dict(release=str(release),previous=str(previous) if previous else None,backup=str(backup),paused_before=paused,activated_at=time.time())
        (root/'deployment-state.json').write_text(json.dumps(state,indent=2))
        return state
    except BaseException:
        hold()
        try:
            manager('stop')
        finally:
            if previous:
                switch(link,previous)
                manager('start')
        # Deliberately leave publishing paused. Additive migrations preserve new history.
        raise


def rollback(root,manager=service,probe=ready):
    root=Path(root);previous=root/'previous'
    if not previous.is_symlink():
        raise RuntimeError('No previous release exists. Use the pre-migration snapshot for first-install recovery.')
    current=(root/'current').resolve();target=previous.resolve()
    hold();manager('stop');snapshot(root)
    switch(root/'current',target)
    try:
        manager('start');probe()
    except BaseException:
        manager('stop');switch(root/'current',current);manager('start');raise
    switch(root/'previous',current)
    return 'Previous release restored. Publishing remains paused; review before resuming.'


def runtime_environment(root):
    root=Path(root).resolve()
    return dict(APP_ENV_FILE=str(root/'.env'),APP_DATA_DIR=str(root/'data'),APP_MEDIA_DIR=str(root/'media'),APP_LOG_DIR=str(root/'logs'),APP_BACKUP_DIR=str(root/'backups'))


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['activate','rollback']);parser.add_argument('--root',required=True);parser.add_argument('--release')
    args=parser.parse_args()
    if args.action=='activate' and not args.release:
        parser.error('--release is required for activation')
    if args.action=='activate':
        print(json.dumps(activate(args.root,args.release),indent=2))
    else:
        print(rollback(args.root))


if __name__=='__main__':
    main()
