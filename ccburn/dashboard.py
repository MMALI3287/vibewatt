"""A local dashboard, served from the standard library.

Deliberately not a desktop GUI. A toolkit like Qt pulls a large binary wheel,
differs per platform and cannot run on a headless cloud box at all. An HTTP
page on localhost renders the same everywhere, needs no build step on Windows,
and works over a port forward when ccburn runs somewhere without a screen.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REFRESH_SCRIPT = """
<script>
/* Re-fetch the whole page rather than patching it: the report is cheap to
   rebuild and this keeps one rendering path instead of two. */
(function () {
  var seconds = %d;
  if (!seconds) return;
  setInterval(function () {
    fetch(location.pathname + '?partial=1', {cache: 'no-store'})
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var next = new DOMParser().parseFromString(html, 'text/html');
        var fresh = next.querySelector('.wrap'), live = document.querySelector('.wrap');
        if (fresh && live) {
          var scroll = [].map.call(document.querySelectorAll('.scroll'),
                                   function (el) { return el.scrollLeft; });
          live.innerHTML = fresh.innerHTML;
          [].forEach.call(document.querySelectorAll('.scroll'), function (el, i) {
            el.scrollLeft = scroll[i] !== undefined ? scroll[i] : el.scrollWidth;
          });
        }
      })
      .catch(function () {});
  }, seconds * 1000);
})();
</script>
"""


def _snapshot(cfg: dict):
    """Rebuild the report from disk. Called per request so the page stays live."""
    from datetime import timezone
    from zoneinfo import ZoneInfo

    from . import history, quota
    from .aggregate import build
    from .ingest import discover
    from .sources import load

    name = cfg.get("timezone", "local")
    if name == "utc":
        tz = timezone.utc
    elif name == "local":
        tz = datetime.now().astimezone().tzinfo
    else:
        try:
            tz = ZoneInfo(name)
        except Exception:
            tz = datetime.now().astimezone().tzinfo

    turns, duplicates = load(discover(cfg))
    report = build(
        turns,
        tz=tz,
        include_sidechains=cfg.get("include_sidechains", True),
        overrides=cfg.get("pricing_overrides"),
        session_hours=cfg.get("session_length_hours", 5),
    )
    if cfg.get("history", True):
        history.merge(report)
        history.restore(report)
    q, _ = quota.read(cfg)
    return report, q, duplicates


def _handler_class(cfg: dict, refresh_seconds: int):
    from .ui import build_dataset, build_page

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):        # keep the console quiet
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/usage":
                    from .cli import serialize

                    report, q, _ = _snapshot(cfg)
                    payload = serialize(report)
                    if q is not None:
                        payload["quota"] = {
                            "source": q.source,
                            "windows": [
                                {
                                    "label": w.label,
                                    "utilization": w.utilization,
                                    "resets_at": w.resets_at.isoformat() if w.resets_at else None,
                                }
                                for w in q.windows
                            ],
                        }
                    self._send(json.dumps(payload, indent=2).encode(), "application/json")
                    return
                if path in ("/", "/index.html"):
                    report, q, dupes = _snapshot(cfg)
                    html = build_page(build_dataset(report, cfg, quota=q, duplicates=dupes))
                    self._send(html.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/dataset":
                    report, q, dupes = _snapshot(cfg)
                    body = json.dumps(build_dataset(report, cfg, quota=q, duplicates=dupes))
                    self._send(body.encode(), "application/json")
                    return
                self._send(b"not found", "text/plain", 404)
            except BrokenPipeError:
                pass
            except Exception as exc:          # a dashboard must not die on one bad read
                self._send(f"error: {exc}".encode(), "text/plain", 500)

    return Handler


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8777,
          refresh_seconds: int = 30, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), _handler_class(cfg, refresh_seconds))
    shown = host if host != "0.0.0.0" else "127.0.0.1"
    url = f"http://{shown}:{port}/"
    print(f"  ccburn dashboard on {url}")
    print(f"  JSON at {url}api/usage")
    if host == "0.0.0.0":
        print("  bound to all interfaces - anyone who can reach this port sees your usage")
    print("  ctrl-c to stop")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        server.server_close()
