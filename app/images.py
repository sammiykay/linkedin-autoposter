import hashlib
from pathlib import Path
from PIL import Image
from app.config import MEDIA_DIR, MAX_IMAGE_BYTES, ALLOWED_IMAGE_EXT

class ImageError(Exception):
    pass

def save_upload(filename: str, data: bytes) -> str:
    """Validate and store an uploaded image. Returns path relative to MEDIA_DIR."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        raise ImageError(f"Unsupported type {ext}. Use JPG, PNG or GIF.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError("Image larger than 8 MB.")
    digest = hashlib.sha256(data).hexdigest()[:16]
    dest = MEDIA_DIR / f"{digest}{ext}"
    import io
    import os
    import tempfile
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 40_000_000:
                raise ValueError('Image dimensions are too large')
            im.verify()
    except Exception:
        raise ImageError('File is not a valid image or exceeds supported dimensions.')
    fd, temporary = tempfile.mkstemp(dir=MEDIA_DIR, prefix='.upload-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, dest)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return dest.name

def resolve(rel: str) -> Path:
    """Resolve a stored image name safely inside MEDIA_DIR (blocks path traversal)."""
    p = (MEDIA_DIR / rel).resolve()
    if MEDIA_DIR.resolve() not in p.parents:
        raise ImageError("Invalid image path.")
    if not p.exists():
        raise ImageError(f"Image not found: {rel}")
    return p
