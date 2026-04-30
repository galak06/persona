"""One-shot OAuth 2.0 helper for the dogfoodandfun-publisher Pinterest app.

Reads PINTEREST_APP_ID + PINTEREST_APP_SECRET from
social-automation/.claude/settings.local.json, runs the Pinterest consent flow
in a browser, captures the redirect on localhost:8765, exchanges the code for
access + refresh tokens, and writes them back into the same settings file.

Run once:
    python social-automation/scripts/pinterest_oauth.py
"""
from __future__ import annotations

import base64
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parents[1] / ".claude" / "settings.local.json"
REDIRECT_URI = "http://localhost:8765/callback"
PORT = 8765
SCOPES = [
    "boards:read",
    "boards:write",
    "pins:read",
    "pins:write",
    "user_accounts:read",
]


def load_env() -> dict:
    return json.loads(SETTINGS_PATH.read_text())["env"]


def save_tokens(access: str, refresh: str) -> None:
    data = json.loads(SETTINGS_PATH.read_text())
    data["env"]["PINTEREST_ACCESS_TOKEN"] = access
    if refresh:
        data["env"]["PINTEREST_REFRESH_TOKEN"] = refresh
    SETTINGS_PATH.write_text(json.dumps(data, indent=2) + "\n")


received: dict[str, str | None] = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — stdlib API
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        received["code"] = qs.get("code", [None])[0]
        received["state"] = qs.get("state", [None])[0]
        received["error"] = qs.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if received["code"]:
            body = (
                "<h2>Pinterest OAuth complete.</h2>"
                "<p>You can close this tab and return to the terminal.</p>"
            )
        else:
            body = f"<h2>OAuth failed</h2><pre>{received.get('error')}</pre>"
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *_a: object, **_kw: object) -> None:
        return


def exchange_code_for_tokens(code: str, client_id: str, client_secret: str) -> dict:
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }
    ).encode()
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        "https://api.pinterest.com/v5/oauth/token",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        print(f"Token exchange failed ({err.code}):\n{err.read().decode()}")
        sys.exit(1)


def main() -> None:
    env = load_env()
    client_id = env.get("PINTEREST_APP_ID")
    client_secret = env.get("PINTEREST_APP_SECRET")
    if not client_id or not client_secret:
        print("Missing PINTEREST_APP_ID / PINTEREST_APP_SECRET in settings.local.json")
        sys.exit(1)

    state = secrets.token_urlsafe(16)
    auth_url = "https://www.pinterest.com/oauth/?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": ",".join(SCOPES),
            "state": state,
        }
    )

    print("Opening Pinterest authorization page in your browser...")
    print(f"  If it doesn't open, paste this URL:\n  {auth_url}\n")
    print(f"Listening on {REDIRECT_URI} for the redirect...")
    webbrowser.open(auth_url)

    try:
        server = http.server.HTTPServer(("127.0.0.1", PORT), CallbackHandler)
    except OSError as e:
        print(f"Could not bind port {PORT}: {e}")
        print("Close whatever else is using that port, or change PORT in this script.")
        sys.exit(1)

    while "code" not in received and "error" not in received:
        server.handle_request()
    server.server_close()

    if received.get("error") or not received.get("code"):
        print(f"OAuth error: {received}")
        sys.exit(1)
    if received.get("state") != state:
        print("State mismatch — aborting (possible CSRF).")
        sys.exit(1)

    print("Got authorization code, exchanging for tokens...")
    resp = exchange_code_for_tokens(received["code"], client_id, client_secret)

    access = resp["access_token"]
    refresh = resp.get("refresh_token", "")
    scope = resp.get("scope", "")
    expires = resp.get("expires_in")

    save_tokens(access, refresh)
    print(f"\n✓ Saved new tokens to {SETTINGS_PATH}")
    print(f"  scope:         {scope}")
    if isinstance(expires, int):
        print(f"  expires:       {expires}s (~{expires // 86400} days)")
    print(f"  refresh token: {'present' if refresh else 'MISSING'}")


if __name__ == "__main__":
    main()
