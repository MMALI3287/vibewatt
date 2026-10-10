"""Closed stdout must not hide failures from files or the store."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vibewatt import cli


@pytest.fixture
def child_env(tmp_path):
    # Path.home patches in conftest do not cross the subprocess boundary.
    (tmp_path / "sitecustomize.py").write_text(
        "import os, socket\n"
        "from pathlib import Path\n"
        "Path.home = classmethod(lambda cls: Path(os.environ['PIPE_TEST_HOME']))\n"
        "def no_network(*args, **kwargs):\n"
        "    raise AssertionError('network forbidden in CLI test')\n"
        "socket.socket.connect = no_network\n"
        "socket.getaddrinfo = no_network\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    for name in list(env):
        if name.startswith(("VIBEWATT_", "CCBURN_")) or name in (
            "CODEX_HOME",
            "CLAUDE_CONFIG_DIR",
            "ANTIGRAVITY_DATA_DIR",
        ):
            env.pop(name)
    env.update(
        PYTHONPATH=str(tmp_path),
        PIPE_TEST_HOME=str(tmp_path / "home"),
        VIBEWATT_DATA_DIR=str(tmp_path / "data"),
        PYTHONIOENCODING="ascii",
    )
    return env


@pytest.fixture(params=["module", "installed"])
def entrypoint(request):
    if request.param == "module":
        return [sys.executable, "-m", "vibewatt.cli"]
    executable = Path(sys.executable).with_name(
        "vibewatt.exe" if os.name == "nt" else "vibewatt"
    )
    assert executable.is_file()
    return [str(executable)]


@pytest.mark.parametrize("unbuffered", [False, True])
@pytest.mark.parametrize(
    "args", [["status", "--json", "--tz", "utc"], ["--help"], ["--version"]]
)
def test_closed_stdout_is_quiet(tmp_path, child_env, entrypoint, args, unbuffered):
    if unbuffered:
        child_env["PYTHONUNBUFFERED"] = "1"
    else:
        child_env.pop("PYTHONUNBUFFERED", None)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        done = subprocess.run(
            [*entrypoint, *args],
            stdout=write_fd,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=tmp_path,
            env=child_env,
            timeout=20,
            check=False,
        )
    finally:
        os.close(write_fd)
    assert done.returncode == 1
    assert done.stderr == b""


def test_redirected_utf8_report(tmp_path, child_env):
    log = tmp_path / "home/.claude/projects/demo/session.jsonl"
    log.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).parent / "fixtures/claude_code_session.jsonl", log)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "vibewatt.cli",
            "report",
            "--offline",
            "--no-quota",
            "--tz",
            "utc",
        ],
        capture_output=True,
        cwd=tmp_path,
        env=child_env,
        timeout=20,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode()
    assert "■" in done.stdout.decode("utf-8")
    assert done.stderr == b""


@pytest.mark.parametrize(
    "error",
    [
        BrokenPipeError("file pipe"),
        ValueError("parse failure"),
        OSError("store failure"),
    ],
)
def test_non_stdout_errors_propagate(monkeypatch, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(cli, "_main", fail)
    with pytest.raises(type(error), match=str(error)):
        cli.main(["status", "--tz", "utc"])


def test_file_output_failure_is_reported(tmp_path, child_env):
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "vibewatt.cli",
            "status",
            "--json",
            "--tz",
            "utc",
            "--out",
            str(tmp_path),
        ],
        capture_output=True,
        cwd=tmp_path,
        env=child_env,
        timeout=20,
        check=False,
    )
    assert done.returncode == 2
    assert b'"code": "output_failed"' in done.stdout


def test_reader_closes_after_receiving_output(tmp_path, child_env, entrypoint):
    log = tmp_path / "home/.claude/projects/demo/session.jsonl"
    log.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).parent / "fixtures/claude_code_session.jsonl", log)
    # Larger than a pipe buffer, so the writer cannot finish before we close it.
    with subprocess.Popen(
        [
            *entrypoint,
            "report",
            "--offline",
            "--no-quota",
            "--tz",
            "utc",
            "--weeks",
            "20000",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=tmp_path,
        env=child_env,
    ) as process:
        assert process.stdout.read(1)
        process.stdout.close()
        process.stdout = None
        _, stderr = process.communicate(timeout=20)
    assert process.returncode == 1
    assert stderr == b""


def test_error_survives_closed_stdout(tmp_path, child_env):
    script = """
from vibewatt import cli

def fail(*args):
    print('buffered output')
    raise ValueError('synthetic parse failure')

cli._main = fail
raise SystemExit(cli.main(['status', '--tz', 'utc']))
"""
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        done = subprocess.run(
            [sys.executable, "-c", script],
            stdout=write_fd,
            stderr=subprocess.PIPE,
            cwd=tmp_path,
            env=child_env,
            timeout=20,
            check=False,
        )
    finally:
        os.close(write_fd)
    assert done.returncode == 1
    assert b"ValueError: synthetic parse failure" in done.stderr
    assert b"BrokenPipeError" not in done.stderr


def test_file_broken_pipe_keeps_output_error(monkeypatch, tmp_path, capsys):
    def fail(*args, **kwargs):
        raise BrokenPipeError("synthetic file pipe")

    monkeypatch.setattr(Path, "write_text", fail)
    assert (
        cli.main(["status", "--json", "--tz", "utc", "--out", str(tmp_path / "out")])
        == 2
    )
    assert '"code": "output_failed"' in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["date", "store"])
def test_snapshot_errors_keep_exit_two(tmp_path, child_env, failure):
    args = ["status", "--json", "--tz", "utc"]
    if failure == "date":
        args += ["--since", "not-a-date"]
    else:
        database = Path(child_env["VIBEWATT_DATA_DIR"]) / "vibewatt.db"
        database.parent.mkdir(parents=True)
        database.write_bytes(b"not a SQLite database")
    done = subprocess.run(
        [sys.executable, "-m", "vibewatt.cli", *args],
        capture_output=True,
        cwd=tmp_path,
        env=child_env,
        timeout=20,
        check=False,
    )
    assert done.returncode == 2
    assert b'"code": "snapshot_failed"' in done.stdout
