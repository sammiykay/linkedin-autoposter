from datetime import datetime, timedelta
import pytest
from app import config, db, linkedin, worker


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'posts.db')
    db.init()
    return db


def due_post(queue):
    pid = queue.add_post('Test post', queue.now_str(), 'scheduled')
    return queue.claim_due(24, limit=1)[0]


def test_publish_success_records_urn(queue, monkeypatch):
    post = due_post(queue)
    monkeypatch.setattr(linkedin, 'publish', lambda *args, **kwargs: 'urn:li:share:test')
    worker.process_one(post)
    saved = queue.get(post['id'])
    assert saved['status'] == 'posted'
    assert saved['post_urn'] == 'urn:li:share:test'
    assert saved['posted_at']


def test_temporary_error_retries_and_token_error_stops(queue, monkeypatch):
    post = due_post(queue)
    def temporary(*args, **kwargs):
        raise linkedin.LinkedInError('Temporary failure', retryable=True)
    monkeypatch.setattr(linkedin, 'publish', temporary)
    worker.process_one(post)
    assert queue.get(post['id'])['status'] == 'scheduled'
    assert queue.get(post['id'])['next_attempt_at'] > queue.now_str()
    def expired(*args, **kwargs):
        raise linkedin.TokenError('Renew your token')
    monkeypatch.setattr(linkedin, 'publish', expired)
    worker.process_one(post)
    assert queue.get(post['id'])['status'] == 'scheduled'
    assert queue.publishing_control()


def test_claim_one_marks_overdue_without_delaying_valid_posts(queue):
    old = (datetime.now() - timedelta(days=3)).strftime(queue.FMT)
    for _ in range(3):
        queue.add_post('Overdue', old, 'scheduled')
    valid = due_post(queue)
    assert valid['text'] == 'Test post'
    assert sum(p['status'] == 'missed' for p in queue.all_posts()) == 3
