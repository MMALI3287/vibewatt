from datetime import UTC, datetime, timedelta

from vibewatt import cli, quota, store


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
