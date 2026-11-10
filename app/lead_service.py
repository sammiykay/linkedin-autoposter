"""Durable assisted outreach: no LinkedIn requests or automatic message sends."""
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from app import db, alerts

FIELDS = ['full_name','linkedin_url','external_id','first_name','last_name','email','phone','company','job_title','industry','company_size','country','city','website','tags','source','notes','message']
SIZES = ['1-10','11-50','51-200','201-500','501-1000','1001+']
VARIABLES = {'full_name','first_name','last_name','company','job_title','campaign_name'}


def migrate(c):
    if c.execute('SELECT 1 FROM schema_migrations WHERE version=3').fetchone(): return
    statements = [
        "CREATE TABLE IF NOT EXISTS lead_contacts(id INTEGER PRIMARY KEY, profile TEXT NOT NULL UNIQUE, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', due REAL, updated REAL NOT NULL)",
        'CREATE TABLE IF NOT EXISTS lead_history(id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL)',
        'CREATE TABLE lead_campaigns(id INTEGER PRIMARY KEY, data TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1)',
        "CREATE TABLE lead_enrollments(id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL, campaign_id INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'active', step INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL)",
        "CREATE UNIQUE INDEX one_active_lead_campaign ON lead_enrollments(lead_id) WHERE state IN ('active','paused')",
        "CREATE TABLE lead_tasks(id INTEGER PRIMARY KEY, enrollment_id INTEGER NOT NULL, step INTEGER NOT NULL, due REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', draft TEXT, version INTEGER NOT NULL DEFAULT 1, sent_at REAL, UNIQUE(enrollment_id,step))",
        'CREATE INDEX lead_task_due ON lead_tasks(state,due)',
        'CREATE TABLE lead_imports(id TEXT PRIMARY KEY, digest TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL, result TEXT)',
        'CREATE TABLE lead_requests(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL)',
        'CREATE TABLE lead_digests(campaign_id INTEGER NOT NULL, day TEXT NOT NULL, outbox_id INTEGER NOT NULL, PRIMARY KEY(campaign_id,day))',
    ]
    for sql in statements: c.execute(sql)
    c.execute('INSERT INTO schema_migrations VALUES(3,?)',(db.now_str(),))
    db.audit(c,'schema.migrated',detail='Assisted leads workspace v3',actor='system')


def event(c,lid,action,detail=''):
    c.execute('INSERT INTO lead_history(lead_id,action,detail,created) VALUES(?,?,?,?)',(lid,action,detail,time.time()))
    db.audit(c,'lead.'+action,detail=f'Contact {lid}')


def contact(data):
    if not isinstance(data,dict): raise ValueError('Contact must be an object.')
    out={key:str(data.get(key,'') or '').strip() for key in FIELDS+['draft']}
    if not out['full_name'] or len(out['full_name'])>200: raise ValueError('Full name is required, up to 200 characters.')
    for key,value in out.items():
        if len(value)>(5000 if key in ('notes','draft','message') else 500): raise ValueError(f'{key} is too long.')
    url=urlsplit(out['linkedin_url'])
    if url.scheme!='https' or url.netloc.lower() not in ('linkedin.com','www.linkedin.com') or not re.fullmatch(r'/in/[A-Za-z0-9_%\-]+/?',url.path):
        raise ValueError('Use an HTTPS LinkedIn /in/ profile URL.')
    out['linkedin_url']='https://www.linkedin.com'+url.path.rstrip('/').lower()
    if out['company_size'] and out['company_size'] not in SIZES: raise ValueError('Choose a supported company-size band.')
    return out


def campaign(data):
    if not isinstance(data,dict): raise ValueError('Campaign must be an object.')
    name=str(data.get('name','')).strip()
    if not name or len(name)>200: raise ValueError('Campaign name is required, up to 200 characters.')
    zone=str(data.get('timezone',''))
    try: ZoneInfo(zone)
    except (ValueError,KeyError): raise ValueError('Choose a valid IANA timezone.') from None
    days=data.get('weekdays',[0,2,4])
    if not isinstance(days,list) or not days or any(type(x)!=int or x not in range(7) for x in days): raise ValueError('Select at least one workday.')
    clock=str(data.get('time','09:00'))
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',clock): raise ValueError('Use a valid HH:MM reminder time.')
    steps=data.get('steps',[])
    if not isinstance(steps,list) or not 1<=len(steps)<=10: raise ValueError('Use between one and ten sequence steps.')
    cleaned=[]
    for i,s in enumerate(steps):
        if not isinstance(s,dict): raise ValueError('Invalid sequence step.')
        template=str(s.get('template','')).strip(); delay=s.get('delay_days',0)
        if not template or len(template)>5000 or type(delay)!=int or not 0<=delay<=365: raise ValueError('Each step needs text and a delay from 0 to 365 days.')
        unknown=set(re.findall(r'{{\s*(\w+)\s*}}',template))-VARIABLES
        if unknown: raise ValueError('Unknown placeholder: '+', '.join(sorted(unknown)))
        # Reject malformed expressions rather than silently sending unresolved text.
        if '{{' in re.sub(r'{{\s*\w+\s*}}','',template) or '}}' in re.sub(r'{{\s*\w+\s*}}','',template): raise ValueError('Invalid template placeholder.')
        cleaned.append({'template':template,'delay_days':0 if i==0 else delay})
    if not isinstance(data.get('criteria',{}),dict): raise ValueError('Buyer criteria must be an object.')
    criteria={}
    for key in ('job_title','industry','company_size','country'):
        values=data.get('criteria',{}).get(key,[])
        if not isinstance(values,list) or len(values)>50: raise ValueError('Buyer criteria must be lists, up to 50 values.')
        criteria[key]=list(dict.fromkeys(str(v).strip()[:100] for v in values if str(v).strip()))
    if 'email_digest' in data and type(data['email_digest']) is not bool: raise ValueError('email_digest must be true or false.')
    return dict(name=name,description=str(data.get('description',''))[:2000],timezone=zone,weekdays=sorted(set(days)),time=clock,steps=cleaned,criteria=criteria,email_digest=bool(data.get('email_digest',False)))


def next_slot(data,earliest):
    zone=ZoneInfo(data['timezone']); start=datetime.fromtimestamp(earliest,zone)
    hour,minute=map(int,data['time'].split(':'))
    for offset in range(15):
        day=start.date()+timedelta(days=offset)
        if day.weekday() not in data['weekdays']: continue
        local=datetime(day.year,day.month,day.day,hour,minute,tzinfo=zone)
        # Round-trip moves nonexistent spring-forward times into the next real hour.
        stamp=local.timestamp()
        if stamp>=earliest: return stamp
    raise ValueError('Could not calculate campaign workday.')


def fit(lead,criteria):
    weights={'job_title':40,'industry':30,'company_size':20,'country':10}; reasons=[]; earned=total=0
    for key,weight in weights.items():
        choices=criteria.get(key,[])
        if not choices: continue
        total+=weight; value=lead.get(key,'').strip().casefold()
        matched=bool(value) and any((x.casefold() in value if key=='job_title' else x.casefold()==value) for x in choices)
        if matched: earned+=weight
        reasons.append({'field':key,'result':'matched' if matched else 'missing' if not value else 'not matched','weight':weight})
    score=round(earned/total*100) if total else None
    return {'score':score,'band':'not configured' if score is None else 'high' if score>=70 else 'medium' if score>=40 else 'low','reasons':reasons}


def render(template,lead,name):
    values=dict(lead,campaign_name=name); missing=[]
    def replace(match):
        key=match.group(1);value=values.get(key,'')
        if not value: missing.append(key)
        return value or match.group(0)
    text=re.sub(r'{{\s*(\w+)\s*}}',replace,template)
    return text,sorted(set(missing))


def stop(c,lid,state):
    c.execute("UPDATE lead_tasks SET state='cancelled',version=version+1 WHERE state='pending' AND enrollment_id IN (SELECT id FROM lead_enrollments WHERE lead_id=? AND state IN ('active','paused'))",(lid,))
    c.execute("UPDATE lead_enrollments SET state=? WHERE lead_id=? AND state IN ('active','paused')",(state,lid))


def cycle(now=None):
    """One owner-only digest per campaign/local day, independent of posting controls."""
    now=time.time() if now is None else now
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        for row in c.execute('SELECT * FROM lead_campaigns WHERE paused=0').fetchall():
            data=json.loads(row['data']);local=datetime.fromtimestamp(now,ZoneInfo(data['timezone']))
            if not data['email_digest'] or local.weekday() not in data['weekdays'] or local.strftime('%H:%M')<data['time']: continue
            count=c.execute("SELECT count(*) FROM lead_tasks t JOIN lead_enrollments e ON e.id=t.enrollment_id WHERE e.campaign_id=? AND e.state='active' AND t.state='pending' AND t.due<=?",(row['id'],now)).fetchone()[0]
            if not count or c.execute('SELECT 1 FROM lead_digests WHERE campaign_id=? AND day=?',(row['id'],local.date().isoformat())).fetchone(): continue
            if not alerts.configured(): continue
            oid=alerts.send('Lead follow-ups ready',f'{count} assisted outreach tasks are due in campaign #{row["id"]}. Open Postdesk → Leads → Due today. Review each draft and send on LinkedIn; messages are never sent automatically.',c=c)
            c.execute('INSERT INTO lead_digests VALUES(?,?,?)',(row['id'],local.date().isoformat(),oid))
