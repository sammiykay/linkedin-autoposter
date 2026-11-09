"""One-time (and every ~55 days) OAuth flow. Run:  python -m app.auth"""
import json
import os
import tempfile
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs
import requests
from app import config

def main():
    if not config.CLIENT_ID or not config.CLIENT_SECRET:
        raise SystemExit("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in .env first.")
    state = secrets.token_urlsafe(16)
    result = {}
    port = urlparse(config.REDIRECT_URI).port or 8000

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [None])[0] == state and "code" in q:
                result["code"] = q["code"][0]
                self.send_response(200); self.end_headers()
                self.wfile.write(b"Authorized. You can close this tab.")
            else:
                self.send_response(400); self.end_headers()
        def log_message(self, *a):
            pass

    url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
        "response_type": "code", "client_id": config.CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI, "state": state, "scope": config.SCOPES})
    print("Open this URL if the browser does not open:\n", url)
    webbrowser.open(url)
    HTTPServer(("localhost", port), Handler).handle_request()
    if "code" not in result:
        raise SystemExit("Authorization failed or state mismatch.")

    r = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data={
        "grant_type": "authorization_code", "code": result["code"],
        "redirect_uri": config.REDIRECT_URI,
        "client_id": config.CLIENT_ID, "client_secret": config.CLIENT_SECRET}, timeout=30)
    r.raise_for_status()
    data = r.json()
    data["issued_at"] = int(time.time())
    config.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=config.TOKEN_PATH.parent, prefix='.token-')
    try:
        with os.fdopen(fd, 'w') as file:
            file.write(json.dumps(data)); file.flush(); os.fsync(file.fileno())
        os.replace(temporary, config.TOKEN_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    from app import db, linkedin
    linkedin.validate_connection()
    db.init()
    db.set_control(actor='oauth', auth_blocked=0, auth_reason='')
    print(f"Saved token. Expires in {data.get('expires_in', 0) // 86400} days.")

if __name__ == "__main__":
    main()
