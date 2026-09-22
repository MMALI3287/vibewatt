"""d5 probes: service status (PLAN 7.12). Only a localhost drip server is contacted."""

from __future__ import annotations

import http.server
import threading
import time

from vibewatt import service_status


class Drip(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"status":{"indicator":"none","description":"Operational"}}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for i in range(0, len(body), 12):  # one chunk per second, each under the 3 s timeout
            self.wfile.write(body[i:i + 12])
            self.wfile.flush()
            time.sleep(1.0)

    def log_message(self, *a):
        pass


def test_timeout_is_per_socket_read_not_total(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(service_status, "URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(service_status, "_expires", 0)
    start = time.monotonic()
    result = service_status.read()
    elapsed = time.monotonic() - start
    srv.shutdown()
    print("DRIP elapsed", round(elapsed, 2), "result", result)
    assert elapsed <= 3.5  # spec: times out in 3 seconds
