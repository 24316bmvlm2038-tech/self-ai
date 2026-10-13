"""Password protection for when Self-AI is reachable from the internet (e.g. through a tunnel).

- With SELFAI_PASSWORD set, visitors sign in once on a login page (a 30-day cookie). Requests
  from this device itself (the phone's own browser) don't need the password.
- Without a password, requests that arrive through a tunnel or proxy are refused, so Self-AI
  is never exposed to the internet by accident.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import time
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

SESSION_COOKIE = "selfai_session"
SESSION_SECONDS = 30 * 24 * 3600
# Headers that tunnels and reverse proxies add: their presence means the request came from outside.
PROXY_HEADERS = {b"cf-connecting-ip", b"cf-ray", b"x-forwarded-for", b"forwarded", b"x-real-ip"}
# /api/cron/ checks its own secret (the scheduler can't sign in).
PUBLIC_PREFIXES = ("/static/", "/sw.js", "/login", "/api/cron/")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
MAX_FAILURES, LOCKOUT_SECONDS = 10, 600


def session_token(password: str) -> str:
    return hmac.new(password.encode(), b"selfai-session-v1", hashlib.sha256).hexdigest()


def via_proxy(scope) -> bool:
    return any(name.lower() in PROXY_HEADERS for name, _ in scope.get("headers", []))


def is_local(scope) -> bool:
    client = scope.get("client")
    return bool(client) and client[0] in LOOPBACK and not via_proxy(scope)


def login_page(error: str = "") -> str:
    message = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Self-AI</title>
<link rel="manifest" href="/static/manifest.webmanifest">
<link rel="icon" href="/static/icon-192.png">
<meta name="theme-color" content="#212121">
<style>
  :root {{ color-scheme: light dark; --bg: #fff; --card: #f4f4f4; --text: #0d0d0d; --muted: #6b6b6b; --border: #e0e0e0; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #212121; --card: #2f2f2f; --text: #ececec; --muted: #a0a0a0; --border: #3a3a3a; }} }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg); color: var(--text);
         font: 16px/1.5 ui-sans-serif, -apple-system, system-ui, "Segoe UI", Roboto, sans-serif; }}
  form {{ width: min(340px, calc(100vw - 32px)); background: var(--card); border: 1px solid var(--border); border-radius: 18px; padding: 28px 24px; }}
  img {{ width: 48px; height: 48px; display: block; margin: 0 auto 12px; }}
  h1 {{ font-size: 20px; text-align: center; margin: 0 0 4px; }}
  p {{ text-align: center; color: var(--muted); margin: 0 0 18px; font-size: 14px; }}
  .error {{ color: #e5484d; }}
  input {{ box-sizing: border-box; width: 100%; padding: 12px 14px; border-radius: 12px; border: 1px solid var(--border);
          background: var(--bg); color: var(--text); font-size: 16px; margin-bottom: 12px; }}
  button {{ width: 100%; padding: 12px; border: 0; border-radius: 12px; background: #4d6bfe; color: #fff; font-size: 16px; font-weight: 600; }}
</style></head>
<body><form method="post" action="/login">
  <img src="/static/icon-192.png" alt="">
  <h1>Self-AI</h1>
  <p>Enter your password to continue.</p>
  {message}
  <input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
  <button type="submit">Sign in</button>
</form></body></html>"""


class AuthMiddleware:
    def __init__(self, app, password: str):
        self.app = app
        self.password = password
        self.token = session_token(password) if password else ""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if not self.password:
            if via_proxy(scope):
                message = (
                    "Self-AI was reached from the internet, but no password is set. "
                    "Add SELFAI_PASSWORD to the .env file (or run setup-url.sh) and restart Self-AI."
                )
                return await _send(send, 403, "text/plain; charset=utf-8", message.encode())
            return await self.app(scope, receive, send)
        if is_local(scope) or scope["path"].startswith(PUBLIC_PREFIXES) or self._authorized(scope):
            return await self.app(scope, receive, send)
        if scope["path"].startswith("/api/"):
            body = json.dumps({"detail": "Please sign in."}).encode()
            return await _send(send, 401, "application/json", body)
        return await _send(send, 401, "text/html; charset=utf-8", login_page().encode())

    def _authorized(self, scope) -> bool:
        for name, value in scope.get("headers", []):
            name = name.lower()
            if name == b"cookie":
                for part in value.decode("latin-1").split(";"):
                    key, _, cookie = part.strip().partition("=")
                    if key == SESSION_COOKIE and hmac.compare_digest(cookie, self.token):
                        return True
            elif name == b"authorization" and value[:6].lower() == b"basic ":  # for scripts and API clients
                try:
                    _, _, given = base64.b64decode(value[6:]).partition(b":")
                except ValueError:
                    continue
                if hmac.compare_digest(given, self.password.encode()):
                    return True
        return False


async def _send(send, status: int, content_type: str, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def login_endpoint(password: str):
    token = session_token(password) if password else ""
    failures: list[float] = []

    async def login(request: Request) -> Response:
        if not password:
            return RedirectResponse("/", status_code=303)
        if request.method == "GET":
            return HTMLResponse(login_page())
        now = time.monotonic()
        failures[:] = [t for t in failures if now - t < LOCKOUT_SECONDS]
        if len(failures) >= MAX_FAILURES:
            return HTMLResponse(login_page("Too many wrong passwords. Try again in 10 minutes."), status_code=429)
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        given = (form.get("password") or [""])[0]
        if hmac.compare_digest(given.encode(), password.encode()):
            secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax", secure=secure
            )
            return response
        failures.append(now)
        await asyncio.sleep(1)  # slow down guessing
        return HTMLResponse(login_page("Wrong password."), status_code=401)

    return login
