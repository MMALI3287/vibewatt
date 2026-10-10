import json
import socket
import sys
from datetime import UTC, datetime, timedelta

import pytest

from vibewatt import cli, quota, store


@pytest.mark.parametrize("command", ["status", "quota"])
def test_future_schema_is_a_structured_error(
    command, future_store, monkeypatch, capsys
):
    monkeypatch.setattr(store, "db_path", lambda: future_store)
    before = future_store.read_bytes()

    assert cli.main([command, "--json", "--tz", "utc"]) == 2

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert not captured.err
    assert payload["state"] == "error"
    assert payload["usage"] is None
    assert payload["error"]["code"] == "schema_too_new"
    assert f"schema {store.SCHEMA_VERSION + 1}" in payload["error"]["message"]
    assert "Upgrade vibewatt" in payload["error"]["message"]
    assert "backup" in payload["error"]["message"]
    assert future_store.read_bytes() == before
    assert not list(future_store.parent.glob("*.bak"))


def test_future_schema_is_an_actionable_cli_error(future_store, monkeypatch, capsys):
    monkeypatch.setattr(store, "db_path", lambda: future_store)
    before = future_store.read_bytes()

    assert cli.main(["sessions", "--offline", "--tz", "utc"]) == 2

    captured = capsys.readouterr()
    assert not captured.out
    assert "Upgrade vibewatt" in captured.err
    assert "backup" in captured.err
    assert "Traceback" not in captured.err
    assert future_store.read_bytes() == before


def test_status_json_is_store_only(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not discover logs or access network")

    monkeypatch.setattr(cli, "discover", forbidden)
    monkeypatch.setattr(cli.pricing, "_fetch_remote", forbidden)
    monkeypatch.setattr(quota, "read", forbidden)
    assert cli.main(["status", "--json", "--tz", "utc"]) == 1
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["state"] == "unavailable"
    assert payload["usage"]["coverage"] == "retained_local"


def test_quota_json_uses_retained_sample(capsys):
    now = datetime.now(UTC)
    with store.connect() as conn:
        quota.record(
            conn,
            quota.Quota(
                [quota.Window("five_hour", 23, now + timedelta(hours=2))],
                "statusline",
                now,
            ),
        )
    assert cli.main(["quota", "--json", "--tz", "utc"]) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "available"
    assert payload["quota"]["windows"][0]["utilization_percent"] == 23


def test_quota_disabled_json(capsys):
    assert cli.main(["quota", "--json", "--no-quota", "--tz", "utc"]) == 1
    import json

    assert json.loads(capsys.readouterr().out)["quota"]["reason"] == "disabled"


def test_status_reports_partial_when_usage_exists_without_quota(logs, capsys):
    cli.sync_store({"offline": True}, UTC, list(logs.items()))
    assert cli.main(["status", "--json", "--tz", "utc"]) == 1
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "partial"
    assert payload["usage"]["report"]["total"]["responses"] > 0
    assert payload["usage"]["last_sync_at"]


def test_store_error_is_json(monkeypatch, capsys):
    import sqlite3

    def broken():
        raise sqlite3.OperationalError("test failure")

    monkeypatch.setattr(store, "connect", broken)
    assert cli.main(["quota", "--json", "--tz", "utc"]) == 2
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "error"
    assert payload["error"]["code"] == "snapshot_failed"


def test_status_rejects_different_rollup_timezone(logs, capsys):
    import json

    cli.sync_store({"offline": True}, UTC, list(logs.items()))
    assert (
        cli.main(["status", "--json", "--tz", "Asia/Tokyo", "--since", "2026-09-16"])
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "resync_required"
    assert payload["usage"] is None


def test_status_uses_stable_day_start_timezone(logs, capsys):
    import json

    from vibewatt.config import day_zone

    cli.sync_store({"offline": True}, day_zone(UTC, 6), list(logs.items()))
    cli.main(["status", "--json", "--tz", "utc", "--day-start-hour", "6"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["usage"]["timezone"] == "UTC|0:00:00@6h"


def test_output_write_error_is_structured(tmp_path, capsys):
    import json

    assert (
        cli.main(
            [
                "quota",
                "--json",
                "--tz",
                "utc",
                "--out",
                str(tmp_path / "missing" / "out.json"),
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "error"
    assert payload["error"]["code"] == "output_failed"


def test_status_json_prices_history_from_the_cached_remote_table(monkeypatch, capsys):
    import json

    from vibewatt import pricing

    def forbidden(*args, **kwargs):
        raise AssertionError("must not access network")

    monkeypatch.setattr(pricing, "_fetch_remote", forbidden)
    monkeypatch.setattr(pricing, "_remote", None)
    path = pricing._cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "claude-3-7-sonnet": {
                    "litellm_provider": "anthropic",
                    "input_cost_per_token": 3e-06,
                    "output_cost_per_token": 1.5e-05,
                }
            }
        ),
        encoding="utf-8",
    )
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO history_days (day, model, responses, input)"
            " VALUES ('2026-09-01', 'claude-3-7-sonnet', 1, 1000000)"
        )
    cli.main(["status", "--json", "--tz", "utc"])
    report = json.loads(capsys.readouterr().out)["usage"]["report"]
    assert "claude-3-7-sonnet" not in report.get("unknown_models", [])
    assert report["total"]["cost_usd"] == 3


def test_version_flag_prints_version_without_syncing(monkeypatch, capsys):
    import pytest

    from vibewatt import __version__

    def forbidden(*args, **kwargs):
        raise AssertionError("must not discover logs or access network")

    monkeypatch.setattr(cli, "discover", forbidden)
    monkeypatch.setattr(cli.pricing, "_fetch_remote", forbidden)
    monkeypatch.setattr(quota, "read", forbidden)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"vibewatt {__version__}"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_serve_reports_an_occupied_port(host, capsys):
    """An occupied port must fail with an actionable --port hint (issue #120)."""
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, 0))
    except OSError:
        sock.close()
        pytest.skip(f"{host} is unavailable on this host")
    port = sock.getsockname()[1]
    sock.listen(1)
    try:
        code = cli.main(
            [
                "serve",
                "--host",
                host,
                "--port",
                str(port),
                "--no-browser",
                "--offline",
                "--tz",
                "utc",
            ]
        )
    finally:
        sock.close()

    assert code == 1
    err = capsys.readouterr().err
    assert "--port" in err
    assert "dashboard" not in err


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_an_occupied_port_never_reaches_the_banner_or_the_browser(host, capsys):
    """A failed start must not announce a dashboard, nor open a browser at it.

    The banner and the browser timer both sit after the port check, so their
    absence is the guarantee (PR #134 review). Running without --no-browser keeps
    the browser in play rather than testing around it."""
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, 0))
    except OSError:
        sock.close()
        pytest.skip(f"{host} is unavailable on this host")
    port = sock.getsockname()[1]
    sock.listen(1)
    try:
        code = cli.main(
            ["serve", "--host", host, "--port", str(port), "--offline", "--tz", "utc"]
        )
    finally:
        sock.close()

    assert code == 1
    err = capsys.readouterr().err
    assert "already in use" in err
    assert "dashboard" not in err


def test_the_port_check_is_the_bind_the_server_would_do():
    """The claim and the server are one bind, so the answer cannot differ.

    Also pins that a socket which is merely claimed is not "in use" until it
    listens."""
    listeners = cli._bind_listeners("127.0.0.1", 0)
    assert listeners
    try:
        port = listeners[0].getsockname()[1]
        assert cli._port_in_use("127.0.0.1", port) is False
        listeners[0].listen(1)
        assert cli._port_in_use("127.0.0.1", port) is True
    finally:
        for listener in listeners:
            listener.close()


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows SO_REUSEADDR allows a second bind"
)
def test_a_listening_socket_is_refused_by_the_bind_on_posix():
    """On POSIX the bind refuses a live listener, which is what makes it the check.

    Windows is the exception the connect probe exists for: there SO_REUSEADDR lets a
    second bind succeed over a listening socket, so on Windows the bind can never be
    the authority and `_port_in_use` answers from a connection instead."""
    listeners = cli._bind_listeners("127.0.0.1", 0)
    assert listeners
    try:
        port = listeners[0].getsockname()[1]
        listeners[0].listen(1)
        assert cli._bind_listeners("127.0.0.1", port) is None
    finally:
        for listener in listeners:
            listener.close()


@pytest.mark.skipif(not socket.has_ipv6, reason="no IPv6 on this host")
def test_an_ipv6_socket_stays_ipv6_only():
    """`--host ::` must keep serving exactly what it served before the handover.

    asyncio's create_server turns dual-stack off on IPv6 sockets, and
    create_server(sock=) keeps whatever the socket carries, so the claim has to set
    it too: otherwise `::` starts answering IPv4-mapped connections and collides
    with an existing IPv4 listener that used to coexist (PR #134 review)."""
    listeners = cli._bind_listeners("::", 0)
    if not listeners:
        pytest.skip("the IPv6 wildcard is unavailable on this host")
    try:
        assert listeners[0].family == socket.AF_INET6
        option = listeners[0].getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
        assert option == 1
    finally:
        for listener in listeners:
            listener.close()


def test_a_bind_error_that_is_not_in_use_is_not_a_port_conflict(monkeypatch):
    """A genuine startup failure must not be reported as a busy port (PR #134 review).

    Only EADDRINUSE means "taken": a permission error or any other bind failure has
    to surface as itself, or the user is sent to --port for a problem --port cannot
    fix."""
    import errno
    import socket

    monkeypatch.setattr(cli, "_port_in_use", lambda host, port: False)

    class Denied(socket.socket):
        def bind(self, address):
            raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(cli.socket, "socket", Denied)

    with pytest.raises(OSError) as raised:
        cli.main(
            [
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "8777",
                "--no-browser",
                "--offline",
                "--tz",
                "utc",
            ]
        )
    assert raised.value.errno == errno.EACCES
