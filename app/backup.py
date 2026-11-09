"""Atomic content snapshots with checksum verification and isolated restore."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path
from app import config, db


def checksum(path):
    with open(path,'rb') as file:
        return hashlib.file_digest(file,'sha256').hexdigest()


def verify(folder):
    folder=Path(folder)
    manifest=json.loads((folder/'manifest.json').read_text())
    if manifest.get('format')!=1 or set(manifest.get('files',{}))!={'posts.db','media.tgz'}:
        raise ValueError('Invalid backup manifest')
    for name,digest in manifest['files'].items():
        if checksum(folder/name)!=digest:
            raise ValueError('Backup checksum mismatch: '+name)
    with sqlite3.connect(f'file:{folder / "posts.db"}?mode=ro',uri=True) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('Backup database integrity check failed')
    return manifest


def create():
    config.BACKUP_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(config.BACKUP_DIR,0o700)
    temporary=Path(tempfile.mkdtemp(prefix='.partial-',dir=config.BACKUP_DIR))
    try:
        with sqlite3.connect(db.DB_PATH) as source,sqlite3.connect(temporary/'posts.db') as target:
            source.backup(target)
        # Stored images are content-addressed and immutable. Include all referenced images.
        with sqlite3.connect(temporary/'posts.db') as c:
            names=[r[0] for r in c.execute('SELECT DISTINCT image_path FROM posts WHERE image_path IS NOT NULL')]
        with tarfile.open(temporary/'media.tgz','w:gz') as archive:
            for name in names:
                path=(config.MEDIA_DIR/name).resolve()
                if path.parent!=config.MEDIA_DIR.resolve() or not path.is_file():
                    raise ValueError('A referenced image is missing or invalid')
                archive.add(path,arcname=name,recursive=False)
        manifest=dict(format=1,created_at=time.time(),files={n:checksum(temporary/n) for n in ('posts.db','media.tgz')},scope='Local content backup; credentials excluded')
        (temporary/'manifest.json').write_text(json.dumps(manifest,indent=2))
        for path in temporary.iterdir():
            os.chmod(path,0o600)
        verify(temporary)
        destination=config.BACKUP_DIR/('snapshot-'+time.strftime('%Y%m%d-%H%M%S')+'-'+os.urandom(3).hex())
        os.replace(temporary,destination)
        db.operation('backup','ok',destination.name)
        for path in config.BACKUP_DIR.glob('snapshot-*'):
            try:
                old=json.loads((path/'manifest.json').read_text())
                if old['created_at']<time.time()-14*86400:
                    verify(path); shutil.rmtree(path)
            except (OSError,ValueError,KeyError):
                continue
        return destination
    except Exception as error:
        db.operation('backup','failed',str(error)); raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def restore(folder,destination):
    folder=Path(folder); destination=Path(destination)
    verify(folder)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Restore destination must be empty; stop services before manually installing a restore')
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    media=destination/'media';media.mkdir(exist_ok=True)
    with tarfile.open(folder/'media.tgz','r:gz') as archive:
        for member in archive.getmembers():
            if not member.isfile() or Path(member.name).name!=member.name or member.name in ('.','..'):
                raise ValueError('Unsafe archive entry')
            source=archive.extractfile(member)
            with open(media/member.name,'wb') as target:
                shutil.copyfileobj(source,target)
            os.chmod(media/member.name,0o600)
    shutil.copyfile(folder/'posts.db',destination/'posts.db')
    os.chmod(destination/'posts.db',0o600)
    with sqlite3.connect(destination/'posts.db') as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='sessions'").fetchone():
            c.execute('DELETE FROM sessions')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='publishing_control'").fetchone():
            c.execute('UPDATE publishing_control SET paused=1 WHERE id=1')
    return destination


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['create','verify','restore'])
    parser.add_argument('--snapshot');parser.add_argument('--destination')
    args=parser.parse_args()
    if args.action in ('verify','restore') and not args.snapshot:
        parser.error('--snapshot is required')
    if args.action=='restore' and not args.destination:
        parser.error('--destination is required')
    if args.action=='create':
        db.init();print(create())
    elif args.action=='verify':
        print(json.dumps(verify(args.snapshot)))
    else:
        print(restore(args.snapshot,args.destination))


if __name__=='__main__':
    main()
