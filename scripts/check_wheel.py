"""Install and exercise a release wheel outside the checkout, without Node on PATH."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import venv
import zipfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LEGACY_QUERIES = {
    "turns": (
        "SELECT msg_id,request_id,ts,day,source,project,session,model,input,cache_5m,"
        "cache_1h,cache_read,output,thinking,web_search,sidechain,fast,geo,cost,version,hour"
        " FROM turns WHERE msg_id LIKE 'legacy-%' ORDER BY msg_id,request_id"
    ),
    "rollup": (
        "SELECT hr,day,hour,source,project,model,session,sidechain,responses,input,"
        "cache_5m,cache_1h,cache_read,output,thinking,web_search,cost,unpriced,first_ts,last_ts"
        " FROM rollup WHERE session = 'legacy-local' ORDER BY hr,model"
    ),
    "sessions": "SELECT * FROM sessions WHERE id = 'legacy-cloud'",
    "titles": "SELECT * FROM titles WHERE session = 'legacy-local'",
    "history_days": "SELECT * FROM history_days ORDER BY day,model",
    "user_keep": "SELECT * FROM user_keep ORDER BY id",
}


def legacy_rows(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        table: connection.execute(query).fetchall()
        for table, query in LEGACY_QUERIES.items()
    }


def seed_legacy_store(path: Path) -> dict[str, list[tuple]]:
    """Restore only synthetic data produced by the original 0.3.0 store code."""
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/legacy_030.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    assert not path.exists(), "Legacy seed requires a fresh database"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT value FROM meta WHERE key='schema'"
        ).fetchone() == ("9",)
        rows = legacy_rows(connection)
        assert len(rows["turns"]) == 2 and len(rows["rollup"]) == 2
        assert all(rows.values()), "Legacy fixture must exercise every preserved table"
        return rows


def check_legacy_upgrade(path: Path, before: dict[str, list[tuple]]) -> Path:
    """Check the installed CLI's migration and its recoverable pre-upgrade copy."""
    with closing(sqlite3.connect(path)) as connection:
        version = int(
            connection.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[
                0
            ]
        )
        assert version > 9, "Installed wheel did not migrate the 0.3.0 schema"
        columns = {row[1] for row in connection.execute("PRAGMA table_info(turns)")}
        assert {"web_fetch", "account_id", "machine_id"} <= columns
        assert connection.execute(
            "SELECT COUNT(*) FROM turns WHERE msg_id LIKE 'legacy-%'"
            " AND account_id = 'unknown' AND machine_id != ''"
        ).fetchone() == (2,), "Legacy identity attribution was lost"
        for table, rows in legacy_rows(connection).items():
            assert rows == before[table], f"Migrated {table} data changed"
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    backups = list(path.parent.glob(f"{path.name}.pre-v{version}-*.bak"))
    assert len(backups) == 1, "Expected one automatic pre-migration backup"
    with closing(sqlite3.connect(backups[0])) as connection:
        assert connection.execute(
            "SELECT value FROM meta WHERE key='schema'"
        ).fetchone() == ("9",), "Backup does not contain the old schema"
        columns = {row[1] for row in connection.execute("PRAGMA table_info(turns)")}
        assert not columns & {"web_fetch", "account_id", "machine_id"}
        for table, rows in legacy_rows(connection).items():
            assert rows == before[table], f"Backup {table} data changed"
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    return backups[0]


@contextmanager
def scratch_dir(prefix: str, release_timeout: float = 15.0) -> Iterator[Path]:
    """A temporary directory removed once every process has let go of it.

    On Windows the pip console-script launcher exits before the Python server
    it started, so that child can still hold server.log and the venv open for
    a moment after `process.wait()`. Retry until the handles are released,
    within a bound, instead of failing on the race.
    """
    root = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield root
    finally:
        deadline = time.monotonic() + release_timeout
        while True:
            try:
                shutil.rmtree(root)
                break
            except PermissionError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--hold",
        type=int,
        default=0,
        help="Seconds to keep server alive for external browser verification",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Use the build host browser to verify the installed dashboard",
    )
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "vibewatt/static/index.html" in names
        assert not any(n.endswith(".map") for n in names)
        assets = [
            n.removeprefix("vibewatt/static")
            for n in names
            if n.startswith("vibewatt/static/assets/")
        ]
        assert any(n.endswith(".js") for n in assets) and any(
            n.endswith(".css") for n in assets
        )
    with scratch_dir(prefix="vibewatt wheel 日本語 ") as root:
        envdir = root / "venv"
        venv.EnvBuilder(with_pip=True).create(envdir)
        bindir = envdir / ("Scripts" if os.name == "nt" else "bin")
        python = bindir / ("python.exe" if os.name == "nt" else "python")
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                str(wheel),
            ],
            check=True,
            cwd=root,
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("VIBEWATT_", "CCBURN_", "PYTHON"))
        }
        for key, sub in {
            "HOME": "home",
            "USERPROFILE": "home",
            "APPDATA": "appdata",
            "LOCALAPPDATA": "appdata",
            "XDG_CONFIG_HOME": "config",
            "XDG_DATA_HOME": "data",
            "CLAUDE_CONFIG_DIR": "claude",
            "VIBEWATT_DATA_DIR": "data",
            "VIBEWATT_COWORK_DIR": "cowork",
        }.items():
            env[key] = str(root / sub)
        config = root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "offline": True,
                    "quota": False,
                    "timezone": "utc",
                    "sync_interval_seconds": 0,
                }
            ),
            encoding="utf-8",
        )
        env["VIBEWATT_CONFIG"] = str(config)
        fixture = (
            Path(__file__).resolve().parents[1]
            / "tests/fixtures/claude_code_session.jsonl"
        )
        target = root / "claude/projects/demo/session.jsonl"
        target.parent.mkdir(parents=True)
        shutil.copyfile(fixture, target)
        env["PATH"] = str(bindir)
        assert shutil.which("node", path=env["PATH"]) is None
        executable = bindir / ("vibewatt.exe" if os.name == "nt" else "vibewatt")
        database = root / "data/vibewatt.db"
        legacy = seed_legacy_store(database)
        snapshots = []
        for command in ("sync", "sync", "doctor"):
            subprocess.run(
                [str(executable), command, "--offline", "--no-quota"],
                cwd=root,
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            if command == "sync":
                with closing(sqlite3.connect(database)) as connection:
                    snapshots.append(
                        connection.execute(
                            "SELECT COUNT(*), SUM(input), SUM(output), SUM(cost) FROM turns"
                        ).fetchone()
                    )
        assert snapshots[0] == snapshots[1] and snapshots[0][0] > 0
        check_legacy_upgrade(database, legacy)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", args.port))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with (root / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    str(executable),
                    "serve",
                    "--port",
                    str(port),
                    "--no-browser",
                    "--offline",
                    "--no-quota",
                ],
                cwd=root,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(100):
                    try:
                        with urlopen(base + "/api/health", timeout=2) as response:
                            assert response.status == 200
                        break
                    except (URLError, TimeoutError):
                        if process.poll() is not None:
                            log.seek(0)
                            raise RuntimeError(log.read())
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Server failed to start")
                for path in (
                    "/",
                    "/sessions/s1",
                    "/analysis/findings/example",
                    "/wrapped?year=2026",
                ):
                    with urlopen(
                        Request(base + path, headers={"Accept": "text/html"}), timeout=5
                    ) as response:
                        assert b'id="root"' in response.read()
                for path in assets:
                    with urlopen(base + path, timeout=5) as response:
                        assert response.status == 200
                        if path.endswith(".js"):
                            assert (
                                response.headers.get_content_type() == "text/javascript"
                            )
                        elif path.endswith(".css"):
                            assert response.headers.get_content_type() == "text/css"
                for path in (
                    "/api/usage",
                    "/api/dataset",
                    "/api/missing",
                    "/assets/missing.js",
                ):
                    try:
                        urlopen(
                            Request(base + path, headers={"Accept": "text/html"}),
                            timeout=5,
                        )
                    except HTTPError as error:
                        assert error.code == 404
                    else:
                        raise AssertionError(path)
                with urlopen(base + "/api/summary", timeout=5) as response:
                    summary = json.load(response)
                    assert summary["total"]["responses"] > 0
                for session, title in (
                    ("legacy-local", "Synthetic 日本語 legacy title"),
                    ("legacy-cloud", "Synthetic harvested title"),
                ):
                    with urlopen(
                        base + f"/api/sessions/{session}", timeout=5
                    ) as response:
                        detail = json.load(response)
                        assert detail["title"] == title
                        if session == "legacy-cloud":
                            assert detail["cost"] == 12.345
                check_legacy_upgrade(database, legacy)
                print(
                    f"PASS {sys.platform}: clean wheel, {len(assets)} assets, SPA, APIs, fixture data, 0.3.0 migration and backup, no Node. {base}",
                    flush=True,
                )
                if args.browser:
                    node = shutil.which("node")
                    if not node:
                        raise RuntimeError(
                            "Browser controller requires Node on the build host, not the server PATH"
                        )
                    subprocess.run(
                        [
                            node,
                            str(Path(__file__).with_name("check_dashboard.mjs")),
                            base,
                        ],
                        check=True,
                    )
                if args.hold:
                    time.sleep(args.hold)
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
