"""Persistent email outbox with Resend HTTPS and legacy SMTP delivery."""
import argparse
import hashlib
import json
import math
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
import requests
from app import config, db
from app.log import get_logger
log=get_logger('alerts')


class DeliveryError(Exception):
    """Only sanitized operator messages belong in this exception."""
    def __init__(self,message,retry_after=0):
        super().__init__(message)
        self.retry_after=retry_after


def missing_configuration():
    if config.EMAIL_PROVIDER=='resend':
        required={'RESEND_API_KEY':config.RESEND_API_KEY,'RESEND_FROM':config.RESEND_FROM,'ALERT_TO':config.ALERT_TO}
    elif config.EMAIL_PROVIDER=='smtp':
        required={'SMTP_HOST':config.SMTP_HOST,'ALERT_TO':config.ALERT_TO}
    else:
        return ['EMAIL_PROVIDER (choose resend or smtp)']
    return [name for name,value in required.items() if not value.strip()]


def configured():
    return not missing_configuration()


def send(subject,body,c=None):
    def insert(connection):
        return connection.execute('INSERT INTO alert_outbox(subject,body,next_at,created_at) VALUES(?,?,?,?)',(subject,body,time.time(),time.time())).lastrowid
    if c is not None:
        return insert(c)
    with db.conn() as connection:
        return insert(connection)


def retry_delay(response):
    value=response.headers.get('Retry-After','')
    try:
        delay=float(value)
    except ValueError:
        try:
            delay=(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
        except (ValueError,TypeError,OverflowError):
            return 0
    return min(86400,max(0,delay)) if math.isfinite(delay) else 0


def deliver(subject,body,idempotency_key=None):
    if not configured():
        raise DeliveryError('Email configuration is incomplete')
    if config.EMAIL_PROVIDER=='resend':
        headers={'Authorization':'Bearer '+config.RESEND_API_KEY,'Content-Type':'application/json'}
        if idempotency_key:
            headers['Idempotency-Key']=idempotency_key
        try:
            response=requests.post('https://api.resend.com/emails',headers=headers,
                json={'from':config.RESEND_FROM,'to':[config.ALERT_TO], 'subject':'[Postdesk] '+subject,'text':body},timeout=20)
        except requests.RequestException:
            raise DeliveryError('Resend connection interrupted; delivery remains pending') from None
        if not 200<=response.status_code<300:
            hint='check API key, sender domain, and recipient settings' if response.status_code in (400,401,403,422) else 'delivery will retry'
            raise DeliveryError(f'Resend HTTP {response.status_code}: {hint}',retry_after=retry_delay(response))
        try:
            message_id=response.json()['id']
            if not isinstance(message_id,str) or not message_id:
                raise ValueError()
        except (ValueError,KeyError,TypeError):
            raise DeliveryError('Resend returned an invalid receipt; delivery remains pending') from None
        return message_id
    message=EmailMessage();message['Subject']='[Postdesk] '+subject
    message['From']=config.SMTP_USER or config.ALERT_TO;message['To']=config.ALERT_TO;message.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST,config.SMTP_PORT,timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER,config.SMTP_PASSWORD)
        smtp.send_message(message)
    return None


def delivery_key(row):
    # Includes payload configuration: changing recipient/sender starts a distinct delivery.
    identity=[row['id'],row['created_at'],row['subject'],row['body'],config.EMAIL_PROVIDER,config.RESEND_FROM,config.ALERT_TO]
    return 'postdesk-alert/'+hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()


def flush(only_id=None):
    if not configured():
        return
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("UPDATE alert_outbox SET status='pending' WHERE status='sending' AND next_at<=?",(time.time(),))
        query="SELECT * FROM alert_outbox WHERE status='pending' AND next_at<=?"
        args=[time.time()]
        if only_id is not None:
            query+=' AND id=?';args.append(only_id)
        rows=[dict(r) for r in c.execute(query+' ORDER BY id LIMIT 10',args)]
        for row in rows:
            c.execute("UPDATE alert_outbox SET status='sending',next_at=? WHERE id=?",(time.time()+600,row['id']))
    for row in rows:
        try:
            message_id=deliver(row['subject'],row['body'],idempotency_key=delivery_key(row))
        except Exception as error:
            detail=str(error) if isinstance(error,DeliveryError) else type(error).__name__+': delivery failed'
            log.warning('Alert delivery failed: %s',detail)
            delay=max(min(21600,300*2**min(row['attempts'],7)),getattr(error,'retry_after',0))
            with db.conn() as c:
                c.execute("UPDATE alert_outbox SET status='pending',attempts=attempts+1,error=?,next_at=?,provider=? WHERE id=?",(detail,time.time()+delay,config.EMAIL_PROVIDER,row['id']))
        else:
            with db.conn() as c:
                c.execute("UPDATE alert_outbox SET status='sent',attempts=attempts+1,sent_at=?,error=NULL,provider=?,provider_message_id=? WHERE id=?",(time.time(),config.EMAIL_PROVIDER,message_id,row['id']))


def main():
    parser=argparse.ArgumentParser(description='Check alert configuration or explicitly send yourself a test email')
    parser.add_argument('action',choices=['status','test'])
    args=parser.parse_args()
    missing=missing_configuration()
    print('Email provider: '+config.EMAIL_PROVIDER)
    if missing:
        raise SystemExit('Configure these .env fields: '+', '.join(missing))
    if args.action=='status':
        print('Required settings are present. No API request or email was sent.')
        return
    db.init()
    pid=send('Test notification','Your Postdesk alert delivery is working. This is an operator-requested test email.')
    flush(only_id=pid)
    with db.conn() as c:
        row=c.execute('SELECT status,error FROM alert_outbox WHERE id=?',(pid,)).fetchone()
    if row['status']!='sent':
        raise SystemExit(row['error'] or 'Test email remains pending; check System & settings.')
    print('Provider accepted the test email. Check your inbox and spam folder.')


if __name__=='__main__':
    main()
