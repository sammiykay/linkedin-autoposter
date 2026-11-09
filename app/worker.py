"""Single worker with durable attempt phases and conservative recovery."""
import random
import signal
import time
from datetime import datetime, timedelta
import requests
from app import config, db, linkedin
from app.log import get_logger
from app.runtime import worker_lock

log=get_logger('worker')
_running=True


def _stop(*_):
    global _running
    _running=False
    log.info('Shutdown requested; finishing the current attempt.')


def authorization_ready():
    try:
        linkedin.load_token_data()
    except linkedin.TokenError as error:
        state=db.controls()
        if not state['auth_blocked'] or state['auth_reason']!=str(error):
            db.set_control(actor='worker',auth_blocked=1,auth_reason=str(error))
        return False
    return not db.controls()['auth_blocked']


def process_one(post):
    aid=db.start_attempt(post['id'])
    sending=False
    def before_send():
        nonlocal sending
        db.attempt_phase(aid,'sending')
        sending=True
    try:
        urn=linkedin.publish(post['text'],post.get('image_path'),post.get('alt_text'),before_send=before_send)
        # Commit result and attempt together; a failed commit leaves a sending attempt for recovery.
        db.finish_attempt(aid,'posted',status='posted',post_urn=urn,error=None,posted_at=db.now_str(),next_attempt_at=None)
    except linkedin.TokenError as error:
        db.set_control(actor='worker',auth_blocked=1,auth_reason=str(error))
        db.finish_attempt(aid,'auth_blocked',str(error),status='scheduled',error=str(error),next_attempt_at=None)
    except Exception as error:
        uncertain=(isinstance(error,linkedin.LinkedInError) and error.uncertain) or (sending and not isinstance(error,linkedin.LinkedInError))
        retryable=(isinstance(error,linkedin.LinkedInError) and error.retryable) or (isinstance(error,requests.RequestException) and not sending)
        message=str(error)[:500]
        if uncertain:
            db.finish_attempt(aid,'uncertain',message,status='uncertain',error='Check LinkedIn before resolving. '+message)
        elif retryable and post['attempts']<config.MAX_RETRIES:
            delay=min(3600,300*(2**max(0,post['attempts']-1)))+random.uniform(0,30)
            delay=max(delay,getattr(error,'retry_after',None) or 0)
            when=(datetime.now()+timedelta(seconds=delay)).strftime(db.FMT)
            db.finish_attempt(aid,'retry',message,status='scheduled',error=message,next_attempt_at=when)
        else:
            db.finish_attempt(aid,'failed',message,status='failed',error=message)
        log.warning('Post #%s: %s',post['id'],message)


def run():
    global _running
    _running=True
    with worker_lock():
        db.init(); db.recover_stuck()
        signal.signal(signal.SIGTERM,_stop); signal.signal(signal.SIGINT,_stop)
        log.info('Worker started, polling every %ss',config.CHECK_EVERY_SECONDS)
        while _running:
            try:
                db.heartbeat()
                db.recover_stuck()
                if authorization_ready():
                    while _running and db.controls()['can_publish']:
                        if not authorization_ready():
                            break
                        due=db.claim_due(config.MISSED_AFTER_HOURS,limit=1)
                        if not due:
                            break
                        process_one(due[0]); db.heartbeat()
            except Exception:
                log.exception('Worker cycle failed')
            for _ in range(config.CHECK_EVERY_SECONDS):
                if not _running:
                    break
                time.sleep(1)
        db.heartbeat('stopped')


if __name__=='__main__':
    run()
