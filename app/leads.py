"""Authenticated assisted-outreach API; dashboard supplies auth and CSRF protection."""
import csv
import hashlib
import io
import json
import secrets
import time
import zipfile
from functools import wraps
import pandas as pd
from flask import Blueprint, request, jsonify, Response, send_file
from app import db, alerts
from app import lead_service as svc

bp=Blueprint('leads',__name__)


class LeadConflict(ValueError): pass


@bp.errorhandler(LeadConflict)
def conflict(e): return jsonify(error=str(e)),409


def mutation(fn):
    @wraps(fn)
    def run(*args,**kwargs):
        key=request.headers.get('Idempotency-Key','')
        if not 8<=len(key)<=200: raise ValueError('Supply an Idempotency-Key for this change.')
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict): raise ValueError('Send a JSON object.')
        digest=hashlib.sha256(json.dumps([request.path,request.method,payload],sort_keys=True).encode()).hexdigest()
        with db.conn() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM lead_requests WHERE key=?',(key,)).fetchone()
            if old:
                if old['fingerprint']!=digest: raise LeadConflict('This request key was already used for different content.')
                return jsonify(json.loads(old['result']))
            result=fn(c,payload,*args,**kwargs)
            c.execute('INSERT INTO lead_requests VALUES(?,?,?,?)',(key,digest,json.dumps(result),time.time()))
        return jsonify(result)
    return run


def require(c,table,rid):
    row=c.execute(f'SELECT * FROM {table} WHERE id=?',(rid,)).fetchone()
    if row is None: raise ValueError('Record not found.')
    return row


def page_args():
    page=int(request.args.get('page',1))
    if not 1<=page<=100000: raise ValueError('Invalid page.')
    return page


def contact_json(c,row,campaign_id=None):
    item=dict(row);item['data']=json.loads(item['data']);item['data']['linkedin_url']=item['profile']
    enrollment=c.execute("SELECT e.*,p.data campaign_data FROM lead_enrollments e JOIN lead_campaigns p ON p.id=e.campaign_id WHERE lead_id=? ORDER BY CASE WHEN e.state IN ('active','paused') THEN 0 ELSE 1 END,e.id DESC LIMIT 1",(row['id'],)).fetchone()
    item['enrollment']=dict(enrollment) if enrollment else None
    cid=campaign_id or (enrollment['campaign_id'] if enrollment else None)
    criteria=json.loads(require(c,'lead_campaigns',cid)['data'])['criteria'] if cid else {}
    item['fit']=svc.fit(item['data'],criteria)
    if item['enrollment']: item['enrollment'].pop('campaign_data',None)
    return item


@bp.get('/api/leads')
def listing():
    page=page_args(); search='%'+request.args.get('search','')[:200]+'%'; status=request.args.get('status','');cid=request.args.get('campaign',type=int)
    with db.conn() as c:
        where='(l.data LIKE ? OR l.profile LIKE ?)';args=[search,search]
        if status: where+=' AND l.status=?';args.append(status)
        if cid:
            where+=' AND EXISTS(SELECT 1 FROM lead_enrollments e WHERE e.lead_id=l.id AND e.campaign_id=?)';args.append(cid)
        # Compute scores before pagination so score filtering remains correct.
        rows=[contact_json(c,r,cid) for r in c.execute('SELECT l.* FROM lead_contacts l WHERE '+where+' ORDER BY l.id DESC',args)]
        band=request.args.get('band','')
        if band: rows=[r for r in rows if r['fit']['band']==band]
        return jsonify(items=rows[(page-1)*25:page*25],total=len(rows),page=page)


@bp.post('/api/leads')
@mutation
def create(c,data):
    values=svc.contact(data);profile=values['linkedin_url']
    if c.execute('SELECT 1 FROM lead_contacts WHERE profile=?',(profile,)).fetchone(): raise LeadConflict('This LinkedIn profile already exists.')
    lid=c.execute('INSERT INTO lead_contacts(profile,data,updated) VALUES(?,?,?)',(profile,json.dumps(values),time.time())).lastrowid
    svc.event(c,lid,'created');return {'id':lid}


@bp.put('/api/leads/<int:lid>')
@mutation
def edit(c,data,lid):
    old=require(c,'lead_contacts',lid)
    if data.get('updated')!=old['updated']: raise LeadConflict('This contact changed. Reload before saving.')
    values=svc.contact(data)
    if c.execute('SELECT 1 FROM lead_contacts WHERE profile=? AND id!=?',(values['linkedin_url'],lid)).fetchone(): raise LeadConflict('This LinkedIn profile already exists.')
    c.execute('UPDATE lead_contacts SET profile=?,data=?,updated=? WHERE id=?',(values['linkedin_url'],json.dumps(values),time.time(),lid))
    svc.event(c,lid,'edited');return {'id':lid}


@bp.get('/api/leads/<int:lid>/history')
def history(lid):
    with db.conn() as c:
        require(c,'lead_contacts',lid)
        return jsonify(items=[dict(r) for r in c.execute('SELECT * FROM lead_history WHERE lead_id=? ORDER BY id DESC LIMIT 100',(lid,))])


@bp.post('/api/leads/<int:lid>/action')
@mutation
def lead_action(c,data,lid):
    require(c,'lead_contacts',lid);action=data.get('action')
    if action not in ('replied','stop'): raise ValueError('Use replied or stop.')
    disposition=data.get('disposition','unclassified')
    if disposition not in ('interested','neutral','not_interested','unclassified'): raise ValueError('Invalid reply disposition.')
    note=str(data.get('note','')).strip()
    if len(note)>5000: raise ValueError('Reply note is too long.')
    svc.stop(c,lid,'replied' if action=='replied' else 'stopped')
    old=require(c,'lead_contacts',lid)
    if old['status']=='do_not_contact' and action=='replied': raise LeadConflict('Contact is marked do not contact.')
    c.execute('UPDATE lead_contacts SET status=?,due=NULL,updated=? WHERE id=?',('replied' if action=='replied' else 'do_not_contact',time.time(),lid))
    svc.event(c,lid,action,json.dumps({'disposition':disposition,'note':note}));return {'ok':True}


@bp.get('/api/campaigns')
def campaigns():
    with db.conn() as c:
        return jsonify(items=[dict(r,data=json.loads(r['data'])) for r in c.execute('SELECT * FROM lead_campaigns ORDER BY id DESC')],email_configured=alerts.configured(),email_missing=alerts.missing_configuration(),deliveries=[dict(r) for r in c.execute('SELECT d.campaign_id,d.day,o.status,o.attempts,o.error,o.sent_at FROM lead_digests d JOIN alert_outbox o ON o.id=d.outbox_id ORDER BY o.id DESC LIMIT 20')])


@bp.post('/api/campaigns')
@mutation
def create_campaign(c,data):
    values=svc.campaign(data)
    cid=c.execute('INSERT INTO lead_campaigns(data) VALUES(?)',(json.dumps(values),)).lastrowid
    db.audit(c,'lead.campaign_created',detail=f'Campaign {cid}');return {'id':cid}


@bp.put('/api/campaigns/<int:cid>')
@mutation
def edit_campaign(c,data,cid):
    old=require(c,'lead_campaigns',cid)
    if data.get('revision')!=old['revision']: raise LeadConflict('Campaign changed. Reload before saving.')
    values=svc.campaign(data);previous=json.loads(old['data'])
    enrolled=c.execute("SELECT 1 FROM lead_enrollments WHERE campaign_id=? AND state IN ('active','paused')",(cid,)).fetchone()
    if enrolled and previous['steps']!=values['steps']: raise LeadConflict('Sequence steps cannot change while contacts are enrolled. Complete or stop enrollments first.')
    c.execute('UPDATE lead_campaigns SET data=?,revision=revision+1 WHERE id=?',(json.dumps(values),cid))
    # Existing outstanding deadlines remain intentional; new steps use the new schedule.
    db.audit(c,'lead.campaign_edited',detail=f'Campaign {cid}');return {'id':cid}


@bp.post('/api/campaigns/<int:cid>/pause')
@mutation
def pause_campaign(c,data,cid):
    require(c,'lead_campaigns',cid)
    if type(data.get('paused')) is not bool: raise ValueError('paused must be true or false.')
    c.execute('UPDATE lead_campaigns SET paused=?,revision=revision+1 WHERE id=?',(int(data['paused']),cid))
    db.audit(c,'lead.campaign_paused' if data['paused'] else 'lead.campaign_resumed',detail=f'Campaign {cid}')
    return {'ok':True}


@bp.post('/api/campaigns/<int:cid>/enroll')
@mutation
def enroll(c,data,cid):
    campaign=json.loads(require(c,'lead_campaigns',cid)['data']); ids=data.get('lead_ids',[])
    if not isinstance(ids,list) or not 1<=len(ids)<=500 or any(type(i)!=int for i in ids): raise ValueError('Choose between 1 and 500 contacts.')
    created=[]
    for lid in sorted(set(ids)):
        lead=require(c,'lead_contacts',lid)
        if lead['status']=='do_not_contact': raise LeadConflict('A selected contact is marked do not contact.')
        if c.execute("SELECT 1 FROM lead_enrollments WHERE lead_id=? AND state IN ('active','paused')",(lid,)).fetchone(): raise LeadConflict('A selected contact already has an active or paused campaign.')
        eid=c.execute('INSERT INTO lead_enrollments(lead_id,campaign_id,created) VALUES(?,?,?)',(lid,cid,time.time())).lastrowid
        due=svc.next_slot(campaign,time.time())
        initial=json.loads(lead['data']).get('message','').strip() or None
        c.execute('INSERT INTO lead_tasks(enrollment_id,step,due,draft) VALUES(?,0,?,?)',(eid,due,initial))
        c.execute("UPDATE lead_contacts SET status='enrolled',due=?,updated=? WHERE id=?",(due,time.time(),lid))
        svc.event(c,lid,'enrolled',f'Campaign {cid}');created.append(eid)
    return {'ids':created}


@bp.post('/api/campaigns/enrollments/<int:eid>/action')
@mutation
def enrollment_action(c,data,eid):
    row=require(c,'lead_enrollments',eid); action=data.get('action')
    if row['state'] not in ('active','paused'): raise LeadConflict('Enrollment has ended.')
    if action not in ('pause','resume','complete','stop'): raise ValueError('Invalid enrollment action.')
    state={'pause':'paused','resume':'active','complete':'completed','stop':'stopped'}[action]
    c.execute('UPDATE lead_enrollments SET state=? WHERE id=?',(state,eid))
    if action in ('complete','stop'):
        c.execute("UPDATE lead_tasks SET state='cancelled',version=version+1 WHERE enrollment_id=? AND state='pending'",(eid,))
        c.execute('UPDATE lead_contacts SET due=NULL,updated=? WHERE id=?',(time.time(),row['lead_id']))
    svc.event(c,row['lead_id'],'enrollment_'+action);return {'ok':True}


def task_json(c,row):
    item=dict(row);data=json.loads(row['contact_data']); campaign=json.loads(row['campaign_data'])
    text,missing=svc.render(campaign['steps'][row['step']]['template'],data,campaign['name'])
    if row['draft'] is not None:
        text,missing=svc.render(row['draft'],data,campaign['name'])
    item.update(full_name=data['full_name'],company=data.get('company',''),text=text,missing=missing,campaign_name=campaign['name'],timezone=campaign['timezone'],fit=svc.fit(data,campaign['criteria']))
    item.pop('contact_data');item.pop('campaign_data');return item


TASK_SELECT='''SELECT t.*,e.lead_id,e.campaign_id,e.state enrollment_state,p.paused campaign_paused,p.data campaign_data,l.data contact_data,l.profile,l.status contact_status FROM lead_tasks t JOIN lead_enrollments e ON e.id=t.enrollment_id JOIN lead_campaigns p ON p.id=e.campaign_id JOIN lead_contacts l ON l.id=e.lead_id'''


@bp.get('/api/lead-tasks')
def tasks():
    page=page_args();scope=request.args.get('scope','due');cid=request.args.get('campaign',type=int)
    if scope not in ('due','upcoming','all'): raise ValueError('Invalid task scope.')
    where=" WHERE t.state='pending' AND e.state IN ('active','paused')";args=[]
    if scope=='due':where+=" AND t.due<=? AND e.state='active' AND p.paused=0";args.append(time.time())
    if scope=='upcoming':where+=' AND t.due>?';args.append(time.time())
    if cid:where+=' AND e.campaign_id=?';args.append(cid)
    with db.conn() as c:
        total=c.execute('SELECT count(*) FROM ('+TASK_SELECT+where+')',args).fetchone()[0]
        rows=c.execute(TASK_SELECT+where+' ORDER BY t.due,t.id LIMIT 25 OFFSET ?',(*args,(page-1)*25)).fetchall()
        return jsonify(items=[task_json(c,r) for r in rows],total=total,page=page)


@bp.post('/api/lead-tasks/<int:tid>/action')
@mutation
def task_action(c,data,tid):
    row=c.execute(TASK_SELECT+' WHERE t.id=?',(tid,)).fetchone()
    if not row: raise ValueError('Task not found.')
    if row['state']!='pending' or data.get('version')!=row['version']: raise LeadConflict('Task changed. Refresh before continuing.')
    if row['enrollment_state']!='active' or row['campaign_paused'] or row['contact_status']=='do_not_contact': raise LeadConflict('This outreach is paused or stopped.')
    action=data.get('action');campaign=json.loads(row['campaign_data']);now=time.time()
    if action=='draft':
        text=str(data.get('text','')).strip()
        if not text or len(text)>5000: raise ValueError('Draft must contain 1–5000 characters.')
        c.execute('UPDATE lead_tasks SET draft=?,version=version+1 WHERE id=?',(text,tid))
    elif action=='snooze':
        days=data.get('days',3)
        if type(days)!=int or not 1<=days<=365: raise ValueError('Snooze between 1 and 365 days.')
        due=svc.next_slot(campaign,now+days*86400)
        c.execute('UPDATE lead_tasks SET due=?,version=version+1 WHERE id=?',(due,tid))
        c.execute('UPDATE lead_contacts SET due=? WHERE id=?',(due,row['lead_id']))
    elif action=='sent':
        if data.get('confirmed') is not True: raise ValueError('Confirm you sent this message on LinkedIn.')
        if row['due']>now: raise LeadConflict('This message is not due yet.')
        rendered=task_json(c,row)
        if rendered['missing'] or '{{' in rendered['text'] or '}}' in rendered['text']: raise ValueError('Resolve missing placeholders before marking sent.')
        c.execute("UPDATE lead_tasks SET state='sent',sent_at=?,draft=?,version=version+1 WHERE id=?",(now,rendered['text'],tid))
        step=row['step']+1;due=None
        if step<len(campaign['steps']):
            due=svc.next_slot(campaign,now+campaign['steps'][step]['delay_days']*86400)
            c.execute('INSERT INTO lead_tasks(enrollment_id,step,due) VALUES(?,?,?)',(row['enrollment_id'],step,due))
            c.execute('UPDATE lead_enrollments SET step=? WHERE id=?',(step,row['enrollment_id']))
        else:c.execute("UPDATE lead_enrollments SET state='completed' WHERE id=?",(row['enrollment_id'],))
        c.execute("UPDATE lead_contacts SET status='contacted',due=?,updated=? WHERE id=?",(due,now,row['lead_id']))
    else:raise ValueError('Invalid task action.')
    svc.event(c,row['lead_id'],'task_'+action,json.dumps({'task':tid,'message':rendered['text']}) if action=='sent' else f'Task {tid}');return {'ok':True}


def safe_csv(value):
    value=str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value


@bp.get('/api/lead-imports/template')
def template():
    example={k:'' for k in svc.FIELDS};example.update(full_name='Ada Okafor',first_name='Ada',last_name='Okafor',linkedin_url='https://www.linkedin.com/in/ada-example',company='Northstar Studio',job_title='Founder',industry='Software',company_size='11-50',country='Nigeria',city='Lagos',tags='founder;software',source='Conference',notes='Asked about content planning.',message='Hi {{first_name}}, it was good speaking about content planning. Would you be open to a follow-up?')
    if request.args.get('format')=='xlsx':
        stream=io.BytesIO();pd.DataFrame([example]).to_excel(stream,index=False);stream.seek(0)
        return send_file(stream,download_name='postdesk-leads-template.xlsx',as_attachment=True)
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=svc.FIELDS);writer.writeheader();writer.writerow(example)
    return Response(stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=postdesk-leads-template.csv'})


@bp.get('/api/leads/export')
def export():
    with db.conn() as c: rows=c.execute('SELECT * FROM lead_contacts ORDER BY id').fetchall()
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=svc.FIELDS);writer.writeheader()
    for row in rows:
        data=json.loads(row['data']);data['linkedin_url']=row['profile'];writer.writerow({k:safe_csv(data.get(k,'')) for k in svc.FIELDS})
    return Response(stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=postdesk-leads.csv'})


@bp.post('/api/lead-imports/preview')
def preview_import():
    upload=request.files.get('file')
    if not upload or not upload.filename.lower().endswith(('.csv','.xlsx')): raise ValueError('Choose a CSV or XLSX file.')
    raw=upload.read();digest=hashlib.sha256(raw).hexdigest()
    try:
        if upload.filename.lower().endswith('.csv'):
            header=next(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
            normalized=[x.strip().lower() for x in header]
            if len(set(normalized))!=len(normalized): raise ValueError()
            frame=pd.read_csv(io.BytesIO(raw),dtype=str,keep_default_na=False,nrows=501)
        else:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if sum(i.file_size for i in archive.infolist())>50*1024*1024: raise ValueError()
                # Formulas must never silently become cached values or empty cells.
                from openpyxl import load_workbook
                book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
                try:
                    sheet=book.active
                    header=next(sheet.iter_rows(max_row=1,values_only=True))
                    normalized=[str(x or '').strip().lower() for x in header]
                    if len(set(normalized))!=len(normalized): raise ValueError()
                    for line in sheet.iter_rows(max_row=502):
                        if any(cell.data_type=='f' for cell in line): raise ValueError()
                finally:book.close()
            frame=pd.read_excel(io.BytesIO(raw),dtype=str,keep_default_na=False,nrows=501)
    except Exception: raise ValueError('Could not read spreadsheet. Use CSV/XLSX without formulas and within the size limits.') from None
    frame.columns=[str(x).strip().lower() for x in frame.columns]
    if frame.columns.duplicated().any() or not {'full_name','linkedin_url'}<=set(frame.columns) or not 1<=len(frame)<=500: raise ValueError('Use unique columns, full_name and linkedin_url, and 1–500 rows.')
    errors=[];rows=[];seen=set();duplicates=0
    with db.conn() as c:
        for i,row in frame.iterrows():
            try:
                data=svc.contact(row.to_dict())
                duplicate=data['linkedin_url'] in seen or bool(c.execute('SELECT 1 FROM lead_contacts WHERE profile=?',(data['linkedin_url'],)).fetchone())
                if duplicate:duplicates+=1
                seen.add(data['linkedin_url']);rows.append(data)
            except ValueError as e:errors.append({'row':int(i)+2,'error':str(e)})
        previous=c.execute('SELECT result FROM lead_imports WHERE digest=? AND result IS NOT NULL AND created>? ORDER BY created DESC LIMIT 1',(digest,time.time()-86400)).fetchone()
        token=secrets.token_urlsafe(24)
        if not errors:
            c.execute('DELETE FROM lead_imports WHERE result IS NULL AND created<?',(time.time()-3600,))
            c.execute('INSERT INTO lead_imports(id,digest,data,created) VALUES(?,?,?,?)',(token,digest,json.dumps(rows),time.time()))
    return jsonify(token=token if not errors else None,count=len(frame),duplicates=duplicates,new=len(rows)-duplicates,errors=errors,columns=list(frame.columns),sample=rows[:5],previous=json.loads(previous['result']) if previous else None)


@bp.post('/api/lead-imports/commit')
@mutation
def commit_import(c,data):
    row=require(c,'lead_imports',str(data.get('token','')))
    if row['result']:return json.loads(row['result'])
    if time.time()-row['created']>3600: raise LeadConflict('Import preview expired. Upload again.')
    policy=data.get('duplicates','skip')
    if policy not in ('skip','fill_empty'):raise ValueError('Invalid duplicate policy.')
    previous=c.execute('SELECT 1 FROM lead_imports WHERE digest=? AND result IS NOT NULL AND created>?',(row['digest'],time.time()-86400)).fetchone()
    if previous and data.get('confirm_duplicate') is not True: raise LeadConflict('This file was imported within 24 hours. Confirm reimport explicitly.')
    result={'created':0,'updated':0,'skipped':0}
    for values in json.loads(row['data']):
        old=c.execute('SELECT * FROM lead_contacts WHERE profile=?',(values['linkedin_url'],)).fetchone()
        if old:
            if policy=='fill_empty':
                merged=json.loads(old['data']);changed=False
                for key,value in values.items():
                    if not merged.get(key) and value:merged[key]=value;changed=True
                if changed:
                    c.execute('UPDATE lead_contacts SET data=?,updated=? WHERE id=?',(json.dumps(merged),time.time(),old['id']));svc.event(c,old['id'],'import_filled');result['updated']+=1;continue
            result['skipped']+=1;continue
        lid=c.execute('INSERT INTO lead_contacts(profile,data,updated) VALUES(?,?,?)',(values['linkedin_url'],json.dumps(values),time.time())).lastrowid
        svc.event(c,lid,'imported');result['created']+=1
    c.execute('UPDATE lead_imports SET result=? WHERE id=?',(json.dumps(result),row['id']))
    return result
