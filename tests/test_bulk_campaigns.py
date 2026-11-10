"""Bulk campaign and lead spreadsheet regressions with isolated storage."""
import io
import csv
import json
import time
import uuid
import pytest
from app import config,db
from app.dashboard import create_app
from app.bulk_campaigns import FIELDS

@pytest.fixture
def client():
    client=create_app({'TESTING':True}).test_client();csrf=client.get('/api/session').json['csrf']
    response=client.post('/api/login',json={'password':config.APP_PASSWORD},headers={'X-CSRF-Token':csrf})
    client.csrf=response.json['csrf'];return client


def preview(client,raw,name='campaigns.csv'):
    return client.post('/api/campaign-imports/preview',data={'file':(io.BytesIO(raw),name)},headers={'X-CSRF-Token':client.csrf})


def commit(client,token,**extra):
    key=extra.pop('key',uuid.uuid4().hex)
    return client.post('/api/campaign-imports/commit',json=dict(token=token,**extra),headers={'X-CSRF-Token':client.csrf,'Idempotency-Key':key})


def sheet(rows):
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows);return stream.getvalue().encode()


def row(**extra):
    values=dict(campaign_name='Founders',step='1',message='Hi {{first_name}}',timezone='Africa/Lagos',delay_days='0');values.update(extra);return values


@pytest.mark.parametrize('format',['csv','xlsx'])
def test_template_roundtrip_and_paused_campaign(client,format):
    raw=client.get('/api/campaign-imports/template?format='+format).data
    response=preview(client,raw,'campaigns.'+format)
    assert response.status_code==200,response.json
    p=response.json;assert p['count']==2 and p['campaigns']==1 and p['new']==1 and not p['errors']
    assert not client.get('/api/campaigns').json['items']
    key=uuid.uuid4().hex;result=commit(client,p['token'],key=key)
    assert result.json['created']==1
    assert commit(client,p['token'],key=key).json==result.json
    campaign=client.get('/api/campaigns').json['items'][0]
    assert campaign['paused']==1 and len(campaign['data']['steps'])==2
    assert campaign['data']['steps'][1]['delay_days']==3
    with db.conn() as c:
        assert c.execute('SELECT count(*) FROM lead_enrollments').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM alert_outbox').fetchone()[0]==0


def test_duplicate_files_names_and_unchanged_existing_campaign(client):
    raw=sheet([row()]);p=preview(client,raw).json;commit(client,p['token'])
    campaign=client.get('/api/campaigns').json['items'][0]
    again=preview(client,raw).json
    assert again['duplicates']==1 and again['previous']['created']==1
    assert commit(client,again['token']).status_code==409
    assert commit(client,again['token'],confirm_duplicate=True).json['skipped']==1
    changed=sheet([row(campaign_name='FOUNDERS',message='Changed')]);p=preview(client,changed).json
    assert p['duplicate_names']==['FOUNDERS']
    assert commit(client,p['token']).json['skipped']==1
    assert client.get('/api/campaigns').json['items'][0]==campaign


@pytest.mark.parametrize('rows',[
 [row(step='2',delay_days='3')],
 [row(),row(step='1')],
 [row(),row(step='3',delay_days='3')],
 [row(timezone='Invalid/Zone')],
 [row(),row(step='2',delay_days='3',timezone='UTC')],
 [row(message='{{unknown}}')],
 [row(email_digest='yes')],
 [row(company_sizes='tiny')],
 [row(weekdays='Monday')],
 [row(delay_days='2')],
 [row(),row(step='2',delay_days='')],
 [row(campaign_name='')],
])
def test_validation_blocks_entire_file(client,rows):
    p=preview(client,sheet(rows)).json
    assert p['errors'] and p['token'] is None,p
    assert not client.get('/api/campaigns').json['items']


def test_multi_campaign_grouping_and_atomic_preview(client):
    raw=sheet([row(),row(campaign_name='Designers',timezone='UTC',message='Hello designers'),row(step='2',timezone='',delay_days='7')])
    p=preview(client,raw).json;assert p['campaigns']==2 and p['new']==2
    assert commit(client,p['token']).json['created']==2
    bad=sheet([row(campaign_name='New valid'),row(campaign_name='Invalid',timezone='bogus')])
    p=preview(client,bad).json;assert p['token'] is None
    assert len(client.get('/api/campaigns').json['items'])==2


def test_expiry_csrf_and_cross_import_tokens(client):
    raw=sheet([row()]);p=preview(client,raw).json
    with db.conn() as c:c.execute('UPDATE campaign_imports SET created=?',(time.time()-3601,))
    assert commit(client,p['token']).status_code==409
    assert client.post('/api/campaign-imports/preview',data={'file':(io.BytesIO(raw),'campaigns.csv')}).status_code==403
    assert client.post('/api/campaign-imports/commit',json={'token':p['token']}).status_code==403
    assert client.post('/api/lead-imports/commit',json={'token':p['token']},headers={'X-CSRF-Token':client.csrf,'Idempotency-Key':uuid.uuid4().hex}).status_code==400


def test_limit_500_rows_and_100_campaigns(client):
    assert preview(client,sheet([row(campaign_name='Campaign '+str(i)) for i in range(101)])).status_code==400
    assert preview(client,sheet([row()]*501)).status_code==400


@pytest.mark.parametrize('layout',['simple','full'])
def test_lead_workbook_has_message_and_instructions(client,layout):
    raw=client.get('/api/lead-imports/template?format=xlsx&layout='+layout).data
    from openpyxl import load_workbook
    book=load_workbook(io.BytesIO(raw));assert book.sheetnames==['Contacts','Field guide']
    headers=[cell.value for cell in book.active[1]];assert 'message' in headers
    assert book.active.freeze_panes=='A2'
    result=client.post('/api/lead-imports/preview',data={'file':(io.BytesIO(raw),'leads.xlsx')},headers={'X-CSRF-Token':client.csrf})
    assert result.status_code==200 and not result.json['errors']
    assert '{{first_name}}' in result.json['sample'][0]['message']
