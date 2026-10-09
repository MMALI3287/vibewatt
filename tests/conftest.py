from __future__ import annotations

import ipaddress
import shutil
import socket
import sqlite3
from datetime import timedelta, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
# Fixed +9 rather than ZoneInfo so Windows needs no tzdata; 20:00Z is the next JST day.
JST = timezone(timedelta(hours=9))


@pytest.fixture
def future_store(tmp_path):
    from vibewatt import store

    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO meta VALUES ('schema', ?)", (str(store.SCHEMA_VERSION + 1),)
        )
        conn.execute("CREATE TABLE future_usage (value TEXT)")
        conn.execute("INSERT INTO future_usage VALUES ('synthetic usage')")
    conn.close()
    return path


@pytest.fixture(autouse=True)
def loopback_test_client(monkeypatch):
    """TestClient sends Host: testserver, which the Host allowlist rejects."""
    from starlette.testclient import TestClient

    original = TestClient.__init__

    def init(self, app, base_url="http://127.0.0.1:8777", **kw):
        original(self, app, base_url=base_url, **kw)

    monkeypatch.setattr(TestClient, "__init__", init)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """No test may read the developer's real ~/.claude or write their real store."""
    from vibewatt import pricing

    # Remote rate history is process state; one test's refresh must not price another's.
    monkeypatch.setattr(pricing, "_history", {})
    monkeypatch.setattr(pricing, "_windows", {})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("VIBEWATT_VSCODE_USER_DIRS", str(tmp_path / "vscode-user"))
    monkeypatch.setenv("VIBEWATT_COPILOT_CACHE", str(tmp_path / "copilot-cache.json"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    # Pre-rename names left in a developer shell must not leak into a test.
    for name in (
        "VIBEWATT_COWORK_DIR",
        "VIBEWATT_CONFIG",
        "CCBURN_COWORK_DIR",
        "CCBURN_CONFIG",
        "CCBURN_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VIBEWATT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """Lay the fixtures out where each source's discover() looks."""
    # A separate override root works on macOS without duplicating native discovery elsewhere.
    monkeypatch.setenv("VIBEWATT_COWORK_DIR", str(tmp_path / "cowork"))
    cc = tmp_path / "claude" / "projects" / "demo" / "session.jsonl"
    cw = (
        tmp_path
        / "cowork"
        / "local-agent-mode-sessions"
        / "acct"
        / "space"
        / "id"
        / "audit.jsonl"
    )
    for src, dst in (
        (FIXTURES / "claude_code_session.jsonl", cc),
        (FIXTURES / "cowork_audit.jsonl", cw),
    ):
        dst.parent.mkdir(parents=True)
        shutil.copyfile(src, dst)
    return {"claude-code": cc, "cowork": cw}


def _ensure_local_network_address(address, nodeid, family=None):
    if family == getattr(socket, "AF_UNIX", None):
        return
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if isinstance(host, str):
        if host.rstrip(".").casefold() == "localhost":
            return
        try:
            if ipaddress.ip_address(host.split("%", 1)[0]).is_loopback:
                return
        except ValueError:
            pass
    raise AssertionError(
        f"external network blocked in {nodeid}: {host!r}; "
        "mock the provider/client request instead"
    )


def _guard_resolver(resolver, nodeid):
    def checked(host, *args, **kwargs):
        if host is not None:
            _ensure_local_network_address(host, nodeid)
        return resolver(host, *args, **kwargs)

    return checked


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch, request):
    """Reject external socket destinations; loopback and Unix sockets are test-local."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    nodeid = request.node.nodeid

    def checked_connect(sock, address):
        _ensure_local_network_address(address, nodeid, sock.family)
        return original_connect(sock, address)

    def checked_connect_ex(sock, address):
        _ensure_local_network_address(address, nodeid, sock.family)
        return original_connect_ex(sock, address)

    def checked_sendto(sock, payload, *args):
        if args:
            _ensure_local_network_address(args[-1], nodeid, sock.family)
        return original_sendto(sock, payload, *args)

    monkeypatch.setattr(socket.socket, "connect", checked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", checked_connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", checked_sendto)
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        monkeypatch.setattr(
            socket, name, _guard_resolver(getattr(socket, name), nodeid)
        )
