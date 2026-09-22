"""Local-only guards, first in the middleware stack.

The dashboard listens on loopback, which protects it from other machines but
not from web pages open in the same browser:

- DNS rebinding (A-007): a site whose name resolves to 127.0.0.1 can read
  every response. The Host header still carries the attacker's name, so only
  loopback names are accepted.
- Cross-site requests (A-004, A-047): a page can POST a `text/plain` body with
  no preflight. A state-changing request must come from this origin, and a
  request with a body must be JSON, which a cross-site form cannot send.

Requests with neither `Origin` nor `Sec-Fetch-Site` (curl, scripts) are not
browser-driven, so they cannot be forged by a web page and are allowed.
"""

from __future__ import annotations

import ipaddress
import json

LOOPBACK_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}
SAFE_METHODS = {b"GET", b"HEAD", b"OPTIONS"}


def is_loopback(host: str) -> bool:
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _hostname(value: str) -> str:
    """`127.0.0.1:8777` -> `127.0.0.1`, `[::1]:8777` -> `[::1]`."""
    if value.startswith("["):
        return value[: value.find("]") + 1] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class LocalOnly:
    def __init__(self, app, extra_hosts: set[str] | None = None) -> None:
        self.app = app
        self.hosts = {h.lower() for h in LOOPBACK_NAMES | (extra_hosts or set())}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        host = headers.get(b"host", b"").decode("latin-1").lower()
        if _hostname(host) not in self.hosts:
            return await _reject(send, 421, f"host {host!r} is not allowed")
        method = scope.get("method", "GET").encode()
        if scope["type"] == "http" and method not in SAFE_METHODS:
            reason = self._cross_site(headers, host)
            if reason:
                return await _reject(send, 403, reason)
        return await self.app(scope, receive, send)

    def _cross_site(self, headers: dict, host: str) -> str | None:
        site = headers.get(b"sec-fetch-site")
        origin = headers.get(b"origin")
        # The browser's own verdict wins when it gives one. Origin is compared
        # only without it: a dev proxy rewrites Host, so a same-origin page can
        # arrive with an Origin that differs from the Host the server sees.
        if site is not None:
            if site not in (b"same-origin", b"none"):
                return f"cross-site request refused (Sec-Fetch-Site: {site.decode('latin-1')})"
        elif origin is not None:
            text = origin.decode("latin-1").lower()
            allowed = {f"{scheme}://{host}" for scheme in ("http", "https")}
            if text not in allowed:
                return f"cross-origin request refused (Origin: {text})"
        has_body = headers.get(b"content-length", b"0") not in (b"", b"0") or (
            b"transfer-encoding" in headers)
        content_type = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
        if has_body and content_type != b"application/json":
            return "a request body must be application/json"
        return None


async def _reject(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
