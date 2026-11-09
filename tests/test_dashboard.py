import io
from datetime import datetime, timedelta

import pytest
from app import config, db, images
from app.dashboard import create_app
from app.runtime import worker_lock


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'posts.db')
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'APP_PASSWORD', 'test-password-for-dashboard')
    monkeypatch.setattr(images, 'MEDIA_DIR', tmp_path)
    app = create_app({'TESTING': True})
    return app.test_client()


def sign_in(client):
    csrf = client.get('/api/session').json['csrf']
    response = client.post('/api/login', json={'password': config.APP_PASSWORD}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    return {'X-CSRF-Token': response.json['csrf'], 'Idempotency-Key': __import__('uuid').uuid4().hex}


def test_access_and_csrf(client):
    assert client.get('/').status_code == 200
    assert client.get('/healthz').json == {'status': 'ok'}
    assert client.get('/api/overview').status_code == 401
    csrf = sign_in(client)
    assert client.post('/api/posts', data={'text': 'hello'}).status_code == 403
    response = client.get('/api/overview')
    assert response.status_code == 200
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert client.post('/api/logout', headers=csrf).status_code == 200
    assert client.get('/api/overview').status_code == 401


def test_schedule_edit_and_guard_claimed_post(client):
    csrf = sign_in(client)
    future = (datetime.now() + timedelta(hours=2)).isoformat(timespec='seconds')
    response = client.post('/api/posts', data={'text': 'hello', 'status': 'scheduled', 'scheduled_at': future}, headers=csrf)
    assert response.status_code == 201
    pid = response.json['id']
    assert db.get(pid)['status'] == 'scheduled'
    db.update(pid, status='posting')
    assert client.put(f'/api/posts/{pid}', data={'text': 'changed'}, headers=csrf).status_code == 409
    assert client.delete(f'/api/posts/{pid}', headers=csrf).status_code == 409
    assert db.get(pid)['text'] == 'hello'


def test_reject_invalid_and_past_schedule(client):
    csrf = sign_in(client)
    for data in ({'text': ''}, {'text': 'x' * 3001}, {'text': 'hi', 'status': 'scheduled', 'scheduled_at': '2000-01-01T09:00'}):
        assert client.post('/api/posts', data=data, headers=csrf).status_code == 400
    assert not db.all_posts()


def test_import_is_atomic_and_draft_default(client):
    csrf = sign_in(client)
    bad = b'text,scheduled_at\ngood,\nbad,2000-01-01 09:00:00\n'
    assert client.post('/api/import', data={'file': (io.BytesIO(bad), 'posts.csv')}, headers=csrf).status_code == 400
    assert not db.all_posts()
    good = b'text,scheduled_at\nhello,\nworld,\n'
    response = client.post('/api/import', data={'file': (io.BytesIO(good), 'posts.csv')}, headers=csrf)
    assert response.status_code == 201
    assert response.json['count'] == 2
    assert all(p['status'] == 'draft' for p in db.all_posts())


def test_bad_image_and_path(client):
    csrf = sign_in(client)
    response = client.post('/api/posts', data={'text': 'hi', 'image': (io.BytesIO(b'not an image'), 'fake.png')}, headers=csrf)
    assert response.status_code == 400
    assert client.get('/media/../../.env').status_code in (400, 404)


def test_login_rate_limit(client):
    csrf = client.get('/api/session').json['csrf']
    for _ in range(10):
        assert client.post('/api/login', json={'password': 'wrong'}, headers={'X-CSRF-Token': csrf}).status_code == 401
    assert client.post('/api/login', json={'password': 'wrong'}, headers={'X-CSRF-Token': csrf}).status_code == 429


def test_worker_lock_and_health(client):
    assert not db.worker_health()['online']
    with worker_lock():
        with pytest.raises(RuntimeError):
            with worker_lock():
                pass
    db.heartbeat()
    assert db.worker_health()['online']
    db.heartbeat('stopped')
    assert not db.worker_health()['online']
    with worker_lock():
        pass
