import time
from unittest.mock import Mock
import pytest
import requests
from app import alerts,config,db
from app.dashboard import create_app


@pytest.fixture
def resend(monkeypatch):
    monkeypatch.setattr(config,'EMAIL_PROVIDER','resend')
    monkeypatch.setattr(config,'RESEND_API_KEY','re_private_test_key')
    monkeypatch.setattr(config,'RESEND_FROM','Postdesk <alerts@example.test>')
    monkeypatch.setattr(config,'ALERT_TO','owner@example.test')


def test_resend_https_request_and_receipt(resend,monkeypatch):
    post=Mock(return_value=Mock(status_code=200,json=lambda:{'id':'resend-message-id'}))
    monkeypatch.setattr(alerts.requests,'post',post)
    pid=alerts.send('Worker recovered','Your worker is running.')
    alerts.flush()
    args=post.call_args
    assert args.args==('https://api.resend.com/emails',)
    assert args.kwargs['timeout']==20
    assert args.kwargs['headers']['Authorization']=='Bearer re_private_test_key'
    assert args.kwargs['headers']['Idempotency-Key'].startswith('postdesk-alert/')
    assert args.kwargs['json']=={'from':config.RESEND_FROM,'to':[config.ALERT_TO],'subject':'[Postdesk] Worker recovered','text':'Your worker is running.'}
    with db.conn() as c:
        row=c.execute('SELECT * FROM alert_outbox WHERE id=?',(pid,)).fetchone()
    assert row['status']=='sent' and row['provider']=='resend' and row['provider_message_id']=='resend-message-id'


def test_retry_reuses_key_and_respects_rate_limit(resend,monkeypatch):
    responses=[Mock(status_code=429,headers={'Retry-After':'7200'}),Mock(status_code=200,json=lambda:{'id':'accepted'})]
    post=Mock(side_effect=responses);monkeypatch.setattr(alerts.requests,'post',post)
    pid=alerts.send('Incident','Please review the worker.')
    alerts.flush()
    with db.conn() as c:
        row=c.execute('SELECT * FROM alert_outbox WHERE id=?',(pid,)).fetchone()
        assert row['status']=='pending' and row['next_at']>=time.time()+7198
        assert 'HTTP 429' in row['error']
        c.execute('UPDATE alert_outbox SET next_at=0 WHERE id=?',(pid,))
    alerts.flush()
    assert post.call_args_list[0].kwargs['headers']['Idempotency-Key']==post.call_args_list[1].kwargs['headers']['Idempotency-Key']
    assert post.call_args_list[0].kwargs['json']==post.call_args_list[1].kwargs['json']


@pytest.mark.parametrize('failure',[401,403,500,'timeout','bad_receipt'])
def test_failures_stay_pending_and_do_not_expose_secrets(resend,monkeypatch,failure):
    if failure=='timeout':
        post=Mock(side_effect=requests.Timeout('sensitive token re_private_test_key'))
    elif failure=='bad_receipt':
        post=Mock(return_value=Mock(status_code=200,json=lambda:{}))
    else:
        post=Mock(return_value=Mock(status_code=failure,headers={},text='secret re_private_test_key'))
    monkeypatch.setattr(alerts.requests,'post',post)
    pid=alerts.send('Incident','Body');alerts.flush()
    with db.conn() as c:row=c.execute('SELECT * FROM alert_outbox WHERE id=?',(pid,)).fetchone()
    assert row['status']=='pending' and row['attempts']==1
    assert config.RESEND_API_KEY not in row['error']
    assert row['provider_message_id'] is None


def test_missing_settings_do_not_send_and_system_never_exposes_key(resend,monkeypatch):
    post=Mock();monkeypatch.setattr(alerts.requests,'post',post)
    monkeypatch.setattr(config,'RESEND_FROM','')
    alerts.send('Incident','Body');alerts.flush();post.assert_not_called()
    assert alerts.missing_configuration()==['RESEND_FROM']
    client=create_app({'TESTING':True}).test_client()
    csrf=client.get('/api/session').json['csrf']
    client.post('/api/login',json={'password':config.APP_PASSWORD},headers={'X-CSRF-Token':csrf})
    response=client.get('/api/system')
    assert response.json['email_provider']=='resend' and response.json['email_missing']==['RESEND_FROM']
    assert config.RESEND_API_KEY not in response.text


def test_different_alerts_have_different_keys_and_targeted_test_flush(resend,monkeypatch):
    post=Mock(return_value=Mock(status_code=200,json=lambda:{'id':'accepted'}));monkeypatch.setattr(alerts.requests,'post',post)
    first=alerts.send('Same subject','Same body');second=alerts.send('Same subject','Same body')
    alerts.flush(only_id=second)
    with db.conn() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM alert_outbox ORDER BY id')]
    assert rows[0]['status']=='pending' and rows[1]['status']=='sent'
    assert alerts.delivery_key(rows[0])!=alerts.delivery_key(rows[1])
    assert post.call_count==1
