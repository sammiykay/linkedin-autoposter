"""Every test gets isolated storage, including logs and credentials."""
import os
import tempfile
import atexit
_bootstrap = tempfile.TemporaryDirectory(prefix='postdesk-unit-')
atexit.register(_bootstrap.cleanup)
os.environ.update(APP_DATA_DIR=_bootstrap.name+'/data', APP_MEDIA_DIR=_bootstrap.name+'/media',
                  APP_LOG_DIR=_bootstrap.name+'/logs', DB_PATH=_bootstrap.name+'/data/posts.db',
                  APP_ENV_FILE=_bootstrap.name+'/.env', TOKEN_PATH=_bootstrap.name+'/data/token.json')
import pytest
from app import config,db,images


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path,monkeypatch):
    for attr,name in [('DATA_DIR','data'),('MEDIA_DIR','media'),('LOG_DIR','logs'),('BACKUP_DIR','backups')]:
        path=tmp_path/name;path.mkdir()
        monkeypatch.setattr(config,attr,path)
    monkeypatch.setattr(db,'DB_PATH',config.DATA_DIR/'posts.db')
    monkeypatch.setattr(config,'TOKEN_PATH',config.DATA_DIR/'token.json')
    monkeypatch.setattr(config,'APP_PASSWORD','isolated-testing-password')
    monkeypatch.setattr(config,'EMAIL_PROVIDER','smtp')
    monkeypatch.setattr(config,'RESEND_API_KEY','')
    monkeypatch.setattr(config,'RESEND_FROM','')
    monkeypatch.setattr(config,'SMTP_HOST','')
    monkeypatch.setattr(config,'ALERT_TO','')
    monkeypatch.setattr(images,'MEDIA_DIR',config.MEDIA_DIR)
    db.init()
