"""Process lifetime lock: released by the OS even after a crash."""
from contextlib import contextmanager
from app import db


@contextmanager
def worker_lock():
    handle = db.DB_PATH.with_name(db.DB_PATH.name + '.worker.lock').open('a+b')
    try:
        try:
            if __import__('os').name == 'nt':
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('Another worker is already running. Stop it before starting a new one.') from error
        yield
    finally:
        handle.close()
