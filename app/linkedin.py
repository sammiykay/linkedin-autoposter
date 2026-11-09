import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import requests
from app import config, images
from app.log import get_logger

log = get_logger("linkedin")
API = "https://api.linkedin.com"

class LinkedInError(Exception):
    def __init__(self, msg, retryable=False, retry_after=None, uncertain=False):
        super().__init__(msg)
        self.retryable = retryable
        self.retry_after = retry_after
        self.uncertain = uncertain

class TokenError(LinkedInError):
    pass

def load_token_data() -> dict:
    try:
        data = json.loads(config.TOKEN_PATH.read_text())
        if not isinstance(data.get('access_token'), str) or not data['access_token']:
            raise ValueError()
        if float(data['issued_at']) + float(data['expires_in']) <= time.time():
            raise ValueError()
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise TokenError('LinkedIn authorization is missing, expired, or invalid. Run python -m app.auth')


def validate_connection():
    try:
        token = load_token_data()['access_token']
        return get_author_urn(token)
    except requests.RequestException:
        raise LinkedInError('Could not reach LinkedIn to validate the connection. Try again.')


def token_days_left() -> int | None:
    try:
        d = load_token_data()
        issued = d.get("issued_at")
        expires_in = d.get("expires_in")
        if issued and expires_in:
            left = (issued + expires_in - time.time()) / 86400
            return int(left)
    except Exception:
        pass
    return None

def _headers(token, json_body=True):
    h = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": config.LINKEDIN_VERSION,
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h

def retry_after(response):
    value = response.headers.get('Retry-After', '')
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return min(86400, max(0, delay)) if __import__('math').isfinite(delay) else None


def _check(r: requests.Response, what: str):
    if r.status_code in (200, 201, 204):
        return
    if r.status_code == 426:
        raise LinkedInError(
            f'{what}: LinkedIn rejected API version {config.LINKEDIN_VERSION} (HTTP 426). '
            'Set LINKEDIN_VERSION to a supported version in .env, restart the worker, '
            'then review and reschedule this failed post.')
    if r.status_code == 401:
        raise TokenError(f'{what}: token rejected. Run python -m app.auth')
    ambiguous = what == 'create post' and (r.status_code >= 500 or r.status_code in (408, 409))
    raise LinkedInError(f'{what}: HTTP {r.status_code}',
                        retryable=r.status_code in (429,500,502,503,504) and not ambiguous,
                        retry_after=retry_after(r), uncertain=ambiguous)


def escape_commentary(text: str) -> str:
    """Escape LinkedIn 'little text' reserved chars. Hashtags (#) are left intact so they stay clickable."""
    return re.sub(r'([\\|{}@\[\]()<>*_~])', r'\\\1', text)

def get_author_urn(token: str) -> str:
    r = requests.get(f"{API}/v2/userinfo", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    _check(r, "userinfo")
    return f"urn:li:person:{r.json()['sub']}"

def upload_image(token: str, author: str, rel_path: str) -> str:
    path = images.resolve(rel_path)
    r = requests.post(
        f"{API}/rest/images?action=initializeUpload",
        headers=_headers(token),
        json={"initializeUploadRequest": {"owner": author}},
        timeout=30)
    _check(r, "initializeUpload")
    val = r.json()["value"]
    upload_url, image_urn = val["uploadUrl"], val["image"]
    with open(path, "rb") as f:
        up = requests.put(upload_url, data=f,
                          headers={"Authorization": f"Bearer {token}"}, timeout=120)
    _check(up, "image upload")
    return image_urn

def publish(text: str, image_path: str | None = None, alt_text: str | None = None, before_send=None) -> str:
    if len(text) > config.MAX_POST_CHARS:
        raise LinkedInError(f"Post is {len(text)} chars, limit is {config.MAX_POST_CHARS}.")
    token = load_token_data()["access_token"]
    author = get_author_urn(token)
    payload = {
        "author": author,
        "commentary": escape_commentary(text),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED",
                         "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if image_path:
        image_urn = upload_image(token, author, image_path)
        media = {"id": image_urn}
        if alt_text:
            media["altText"] = alt_text[:4000]
        payload["content"] = {"media": media}
    if before_send:
        before_send()
    try:
        r = requests.post(f"{API}/rest/posts", headers=_headers(token), json=payload, timeout=30)
    except requests.RequestException:
        raise LinkedInError('Connection interrupted during publication. Check LinkedIn before resolving.', uncertain=True)
    _check(r, "create post")
    urn = r.headers.get("x-restli-id", "")
    if not urn:
        raise LinkedInError('LinkedIn accepted the request without a post identifier. Check LinkedIn.', uncertain=True)
    log.info("Published %s", urn)
    return urn
