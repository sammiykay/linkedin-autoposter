"""Independent monitoring; no browser or publishing worker required."""
import json
import shutil
import time
from datetime import datetime,timedelta
from app import config,db,alerts,linkedin,lead_service


def checks():
    state=db.controls();health=db.worker_health();issues={}
    if not health['online']:
        issues['worker']='Publishing worker heartbeat is stale.'
    try:
        token=linkedin.load_token_data()
        if token['issued_at']+token['expires_in']-time.time()<7*86400:
            issues['authorization']='LinkedIn authorization expires within seven days.'
    except linkedin.TokenError:
        issues['authorization']='LinkedIn authorization is missing, expired, or invalid.'
    if state['auth_blocked']:
        issues['authorization']=state['auth_reason'] or 'Authorization is blocked.'
    with db.conn() as c:
        latest=c.execute("SELECT * FROM operations WHERE kind='backup' ORDER BY id DESC LIMIT 1").fetchone()
        good=c.execute("SELECT * FROM operations WHERE kind='backup' AND status='ok' ORDER BY id DESC LIMIT 1").fetchone()
        count=c.execute("SELECT COUNT(*) FROM posts WHERE status='scheduled' AND scheduled_at<? AND (next_attempt_at IS NULL OR next_attempt_at<?)",((datetime.now()-timedelta(minutes=10)).strftime(db.FMT),db.now_str())).fetchone()[0]
        attention=c.execute("SELECT COUNT(*) FROM posts WHERE status IN ('failed','uncertain')").fetchone()[0]
    if not good or time.time()-good['created_at']>36*3600 or not (config.BACKUP_DIR/good['detail']/'manifest.json').is_file():
        issues['backup']='No completed content backup within 36 hours.'
    if latest and latest['status']=='failed':
        issues['backup']='Latest content backup failed. '+latest['detail']
    if count and state['can_publish']:
        issues['overdue']=f'{count} scheduled posts are more than ten minutes overdue.'
    if attention:
        issues['posts']=f'{attention} posts require review for failed or uncertain publishing.'
    if shutil.disk_usage(config.DATA_DIR).free<1024**3:
        issues['disk']='Less than 1 GiB of free disk space remains.'
    return issues


def run():
    db.init()
    issues=checks()
    try:
        lead_service.cycle()
    except Exception:
        issues['leads']='Lead reminder processing failed. Check service logs.'
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        previous={r['name']:dict(r) for r in c.execute('SELECT * FROM incidents')}
        for name in sorted(set(previous)|set(issues)):
            active=name in issues;detail=issues.get(name,'Recovered')
            old=previous.get(name)
            if old and bool(old['active'])==active and old['detail']==detail:
                continue
            c.execute('INSERT OR REPLACE INTO incidents VALUES(?,?,?,?)',(name,int(active),detail,time.time()))
            alerts.send(('Attention: ' if active else 'Recovered: ')+name,detail,c=c)
        c.execute('INSERT INTO operations(kind,status,detail,created_at) VALUES(?,?,?,?)',('monitor','warning' if issues else 'ok',json.dumps(issues),time.time()))
        # Bound successful historical records, preserving incident and delivery state.
        c.execute("DELETE FROM operations WHERE kind='monitor' AND id NOT IN (SELECT id FROM operations WHERE kind='monitor' ORDER BY id DESC LIMIT 500)")
    alerts.flush()
    return issues


if __name__=='__main__':
    print(json.dumps(run()))
