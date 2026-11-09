from unittest.mock import Mock
import pytest
from app import config, db, linkedin, worker


def test_426_is_actionable_and_not_retried_or_uncertain(monkeypatch):
    monkeypatch.setattr(config,'LINKEDIN_VERSION','202510')
    response=Mock(status_code=426)
    with pytest.raises(linkedin.LinkedInError) as caught:
        linkedin._check(response,'create post')
    error=caught.value
    assert not error.retryable and not error.uncertain
    assert all(text in str(error) for text in ('202510','426','LINKEDIN_VERSION','restart','reschedule'))


def test_version_rejection_keeps_post_failed_until_owner_reschedules(monkeypatch):
    pid=db.add_post('Version rejection',db.now_str(),'scheduled')
    post=db.claim_due(24,1)[0]
    def rejected(*args,**kwargs):
        kwargs['before_send']()
        linkedin._check(Mock(status_code=426),'create post')
    monkeypatch.setattr(linkedin,'publish',rejected)
    worker.process_one(post)
    assert db.get(pid)['status']=='failed'
    assert '426' in db.get(pid)['error']
    assert db.claim_due(24)==[]


def test_version_header_uses_configured_pin(monkeypatch):
    monkeypatch.setattr(config,'LINKEDIN_VERSION','202610')
    assert linkedin._headers('test-token')['Linkedin-Version']=='202610'
