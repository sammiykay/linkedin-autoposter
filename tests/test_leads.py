"""Isolated assisted outreach regression tests. Never contacts LinkedIn."""
import io
import json
import time
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from app import db,config,backup,lead_service as svc
from app.dashboard import create_app

@pytest.fixture
def client():
    app=create_app({'TESTING':True});client=app.test_client()
    csrf=client.get('/api/session').json['csrf']
    r=client.post('/api/login',json={'password':config.APP_PASSWORD},headers={'X-CSRF-Token':csrf})
    client.csrf=r.json['csrf'];return client


def mutate(client,path,data,method='post',key=None):
    return getattr(client,method)(path,json=data,headers={'X-CSRF-Token':client.csrf,'Idempotency-Key':key or uuid.uuid4().hex})


def add(client,slug='ada',**kw):
    result=mutate(client,'/api/leads',dict(full_name='Ada Okafor',linkedin_url=f'https://www.linkedin.com/in/{slug}',first_name='Ada',company='Studio',job_title='Founder',industry='Software',company_size='11-50',country='Nigeria',**kw))
    assert result.status_code==200,result.json
    return result.json['id']


def campaign_data(**kwargs):
    data=dict(name='Founders',timezone='Africa/Lagos',time='09:00',weekdays=list(range(7)),steps=[{'template':'Hi {{first_name}} at {{company}}','delay_days':0},{'template':'Following up {{first_name}}','delay_days':3}],criteria={'job_title':['founder'],'industry':['software'],'company_size':['11-50'],'country':['Nigeria']},email_digest=True)
    data.update(kwargs);return data


def setup_task(client):
    lid=add(client)
    cid=mutate(client,'/api/campaigns',campaign_data()).json['id']
    enrolled=mutate(client,f'/api/campaigns/{cid}/enroll',{'lead_ids':[lid]})
    assert enrolled.status_code==200
    with db.conn() as c:c.execute('UPDATE lead_tasks SET due=?',(time.time()-60,))
    task=client.get('/api/lead-tasks').json['items'][0]
    return lid,cid,task


def test_auth_csrf_and_idempotent_contact(client):
    assert client.post('/api/leads',json={}).status_code==403
    key=uuid.uuid4().hex;data={'full_name':'Ada','linkedin_url':'https://www.linkedin.com/in/ada/'}
    first=mutate(client,'/api/leads',data,key=key)
    assert mutate(client,'/api/leads',data,key=key).json==first.json
    assert mutate(client,'/api/leads',dict(data,full_name='Different'),key=key).status_code==409
    assert mutate(client,'/api/leads',data).status_code==409
    assert client.get('/api/leads').json['total']==1
    for url in ('https://evil.test/in/ada','javascript:alert(1)','https://www.linkedin.com/in/ada/../../x','https://www.linkedin.com:443/in/ada'):
        assert mutate(client,'/api/leads',dict(data,linkedin_url=url)).status_code==400


def test_contacts_edit_history_and_scores(client):
    lid,cid,task=setup_task(client)
    result=client.get('/api/leads?campaign='+str(cid)).json['items'][0]
    assert result['fit']['score']==100
    assert client.get('/api/leads?band=high').json['total']==1
    data=dict(result['data'],updated=result['updated'],notes='Research')
    assert mutate(client,f'/api/leads/{lid}',data,'put').status_code==200
    assert mutate(client,f'/api/leads/{lid}',data,'put').status_code==409
    assert client.get(f'/api/leads/{lid}/history').json['items'][0]['action']=='edited'
    rules={'job_title':['Founder'],'industry':['Software']}
    fit=svc.fit({'job_title':'Co-Founder'},rules)
    assert fit['score']==57 and fit['reasons'][1]['result']=='missing'
    assert svc.fit({}, {})['score'] is None


def test_send_advances_once_and_reply_stops(client):
    lid,cid,task=setup_task(client);tid=task['id'];version=task['version']
    # Merely inspecting tasks never marks them sent.
    assert client.get('/api/lead-tasks').json['items'][0]['state']=='pending'
    assert mutate(client,f'/api/lead-tasks/{tid}/action',{'action':'sent','version':version}).status_code==400
    key=uuid.uuid4().hex;payload={'action':'sent','version':version,'confirmed':True}
    assert mutate(client,f'/api/lead-tasks/{tid}/action',payload,key=key).status_code==200
    assert mutate(client,f'/api/lead-tasks/{tid}/action',payload,key=key).status_code==200
    assert mutate(client,f'/api/lead-tasks/{tid}/action',payload).status_code==409
    upcoming=client.get('/api/lead-tasks?scope=all').json['items']
    assert len(upcoming)==1 and upcoming[0]['step']==1
    assert upcoming[0]['due']>=time.time()+3*86400-10
    assert mutate(client,f'/api/leads/{lid}/action',{'action':'replied','disposition':'interested','note':'Asked for pricing'}).status_code==200
    assert client.get('/api/lead-tasks?scope=all').json['total']==0
    assert client.get('/api/leads').json['items'][0]['status']=='replied'


def test_missing_draft_pause_snooze_and_dnc(client):
    lid,cid,task=setup_task(client)
    assert mutate(client,f'/api/campaigns/{cid}/pause',{'paused':True}).status_code==200
    assert not client.get('/api/lead-tasks').json['items']
    assert mutate(client,f'/api/lead-tasks/{task["id"]}/action',{'action':'draft','version':1,'text':'test'}).status_code==409
    mutate(client,f'/api/campaigns/{cid}/pause',{'paused':False})
    assert mutate(client,f'/api/lead-tasks/{task["id"]}/action',{'action':'draft','version':1,'text':'Hi {{missing}}'}).status_code==200
    assert mutate(client,f'/api/lead-tasks/{task["id"]}/action',{'action':'sent','version':2,'confirmed':True}).status_code==400
    assert mutate(client,f'/api/lead-tasks/{task["id"]}/action',{'action':'snooze','version':2,'days':3}).status_code==200
    mutate(client,f'/api/leads/{lid}/action',{'action':'stop'})
    assert mutate(client,f'/api/campaigns/{cid}/enroll',{'lead_ids':[lid]}).status_code==409


def preview(client,raw,name='leads.csv'):
    return client.post('/api/lead-imports/preview',data={'file':(io.BytesIO(raw),name)},headers={'X-CSRF-Token':client.csrf})


def test_import_atomic_duplicate_and_fill_empty(client):
    invalid=b'full_name,linkedin_url\nAda,https://www.linkedin.com/in/ada\nBad,https://evil.test/x\n'
    assert preview(client,invalid).json['errors'][0]['row']==3
    assert client.get('/api/leads').json['total']==0
    raw=b'full_name,linkedin_url,phone,company\nAda,https://www.linkedin.com/in/ada,+00123,Studio\n'
    p=preview(client,raw).json;assert p['new']==1
    out=mutate(client,'/api/lead-imports/commit',{'token':p['token']})
    assert out.json['created']==1
    p2=preview(client,raw).json
    assert p2['previous']['created']==1
    assert mutate(client,'/api/lead-imports/commit',{'token':p2['token']}).status_code==409
    assert mutate(client,'/api/lead-imports/commit',{'token':p2['token'],'confirm_duplicate':True}).json['skipped']==1
    lead=client.get('/api/leads').json['items'][0]
    assert lead['data']['phone']=='+00123'
    raw2=b'full_name,linkedin_url,email,company\nReplacement,https://www.linkedin.com/in/ada,ada@example.test,Other\n'
    p=preview(client,raw2).json
    assert mutate(client,'/api/lead-imports/commit',{'token':p['token'],'duplicates':'fill_empty'}).json['updated']==1
    lead=client.get('/api/leads').json['items'][0]
    assert lead['data']['company']=='Studio' and lead['data']['email']=='ada@example.test'
    assert "'+00123" in client.get('/api/leads/export').text


def test_templates_xlsx_and_formula_rejection(client):
    raw=client.get('/api/lead-imports/template?format=xlsx').data
    assert preview(client,raw,'leads.xlsx').json['new']==1
    from openpyxl import Workbook
    book=Workbook();book.active.append(['full_name','linkedin_url']);book.active.append(['=1+1','https://www.linkedin.com/in/ada']);stream=io.BytesIO();book.save(stream)
    assert preview(client,stream.getvalue(),'formula.xlsx').status_code==400


def test_schedule_dst_and_campaign_validation(client):
    d=campaign_data(timezone='America/New_York',time='02:30',weekdays=[6])
    start=datetime(2026,3,8,5,tzinfo=timezone.utc).timestamp()
    slot=svc.next_slot(d,start)
    local=datetime.fromtimestamp(slot,svc.ZoneInfo(d['timezone']))
    assert local.hour==3 and local.minute==30
    d['time']='01:30';start=datetime(2026,11,1,4,tzinfo=timezone.utc).timestamp()
    assert svc.next_slot(d,start)==datetime(2026,11,1,5,30,tzinfo=timezone.utc).timestamp()
    for kwargs in ({'weekdays':[]},{'timezone':'bogus'},{'steps':[{'template':'{{bad}}','delay_days':0}]},{'time':'25:00'}):
        assert mutate(client,'/api/campaigns',campaign_data(**kwargs)).status_code==400
    lid,cid,task=setup_task(client)
    revision=client.get('/api/campaigns').json['items'][0]['revision']
    assert mutate(client,f'/api/campaigns/{cid}',dict(campaign_data(steps=[{'template':'changed','delay_days':0}]),revision=revision),'put').status_code==409


def test_independent_digest_dedup_restart_and_backup(client,monkeypatch):
    lid,cid,task=setup_task(client)
    monkeypatch.setattr(config,'EMAIL_PROVIDER','resend');monkeypatch.setattr(config,'RESEND_API_KEY','test');monkeypatch.setattr(config,'RESEND_FROM','owner@example.test');monkeypatch.setattr(config,'ALERT_TO','owner@example.test')
    db.set_control(paused=1,auth_blocked=1)
    now=datetime(2026,11,11,12,tzinfo=timezone.utc).timestamp()
    svc.cycle(now);db.init();svc.cycle(now)
    with db.conn() as c:
        assert c.execute('SELECT count(*) FROM lead_digests').fetchone()[0]==1
        row=c.execute('SELECT * FROM alert_outbox').fetchone();assert 'Ada' not in row['body']
    svc.cycle(now+86400)
    with db.conn() as c:assert c.execute('SELECT count(*) FROM lead_digests').fetchone()[0]==2
    snapshot=backup.create();backup.verify(snapshot)
    restored=config.DATA_DIR.parent/'restored-leads';backup.restore(snapshot,restored)
    import sqlite3
    with sqlite3.connect(restored/'posts.db') as c:
        assert c.execute('SELECT count(*) FROM lead_contacts').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM lead_tasks').fetchone()[0]==1


def test_one_active_enrollment_and_atomic_bulk(client):
    lid,cid,task=setup_task(client);other=add(client,'other')
    assert mutate(client,f'/api/campaigns/{cid}/enroll',{'lead_ids':[other,lid]}).status_code==409
    with db.conn() as c:assert c.execute('SELECT count(*) FROM lead_enrollments').fetchone()[0]==1


def test_concurrent_send_confirmation_creates_one_followup(client):
    lid,cid,task=setup_task(client)
    app=create_app({'TESTING':True})
    def send(_):
        local=app.test_client();csrf=local.get('/api/session').json['csrf']
        response=local.post('/api/login',json={'password':config.APP_PASSWORD},headers={'X-CSRF-Token':csrf})
        local.csrf=response.json['csrf']
        return mutate(local,f'/api/lead-tasks/{task["id"]}/action',{'action':'sent','confirmed':True,'version':task['version']}).status_code
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(send,range(2)))==[200,409]
    with db.conn() as c:
        assert c.execute("SELECT count(*) FROM lead_tasks WHERE state='sent'").fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM lead_tasks WHERE state='pending'").fetchone()[0]==1


def test_daily_digest_missing_configuration_and_concurrent_cycles(client,monkeypatch):
    setup_task(client)
    now=datetime(2026,11,12,12,tzinfo=timezone.utc).timestamp()
    svc.cycle(now)
    with db.conn() as c:assert not c.execute('SELECT 1 FROM lead_digests').fetchone()
    monkeypatch.setattr(config,'EMAIL_PROVIDER','resend');monkeypatch.setattr(config,'RESEND_API_KEY','test');monkeypatch.setattr(config,'RESEND_FROM','owner@example.test');monkeypatch.setattr(config,'ALERT_TO','owner@example.test')
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:svc.cycle(now),range(2)))
    with db.conn() as c:
        assert c.execute('SELECT count(*) FROM lead_digests').fetchone()[0]==1
        c.execute("UPDATE alert_outbox SET status='pending',error='Resend connection interrupted',attempts=1")
    svc.cycle(now)
    status=client.get('/api/campaigns').json
    assert status['deliveries'][0]['error']=='Resend connection interrupted'
    assert status['deliveries'][0]['attempts']==1


def test_duplicate_headers_and_empty_import(client):
    for raw in (b'full_name,linkedin_url,full_name\nAda,https://www.linkedin.com/in/ada,Ada\n',b'full_name,linkedin_url\n'):
        assert preview(client,raw).status_code==400


def test_imported_initial_message_used_for_enrollment(client):
    raw=b'full_name,linkedin_url,first_name,message\nAda,https://www.linkedin.com/in/ada,Ada,Hi {{first_name}} - a personal note\n'
    p=preview(client,raw).json
    assert mutate(client,'/api/lead-imports/commit',{'token':p['token']}).json['created']==1
    lid=client.get('/api/leads').json['items'][0]['id']
    cid=mutate(client,'/api/campaigns',campaign_data()).json['id']
    assert mutate(client,f'/api/campaigns/{cid}/enroll',{'lead_ids':[lid]}).status_code==200
    task=client.get('/api/lead-tasks?scope=all').json['items'][0]
    assert task['text']=='Hi Ada - a personal note' and not task['missing']
