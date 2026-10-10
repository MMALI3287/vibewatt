"""Atomic report output must not truncate an existing destination."""

import errno
import os
import stat

import pytest

from vibewatt import cli
from vibewatt.atomic_output import atomic_write_text


def test_atomic_replacement_preserves_unicode_and_existing_mode(tmp_path):
    destination = tmp_path / "report.json"
    destination.write_bytes(b"original")
    os.chmod(destination, 0o644)

    atomic_write_text(destination, '{"café":"東京"}\n')

    assert destination.read_text(encoding="utf-8") == '{"café":"東京"}\n'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.json"]
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o644


def test_partial_write_failure_keeps_old_report_and_removes_temporary(
    tmp_path, monkeypatch
):
    destination = tmp_path / "report.json"
    destination.write_bytes(b"original report")
    real_write = os.write
    attempts = 0

    def partial_then_fail(fd, data):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return real_write(fd, data[:2])
        raise OSError(errno.ENOSPC, "simulated disk full")

    monkeypatch.setattr(os, "write", partial_then_fail)
    with pytest.raises(OSError, match="simulated disk full"):
        atomic_write_text(destination, "replacement content")

    assert destination.read_bytes() == b"original report"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.json"]


def test_replace_failure_keeps_old_report_and_removes_temporary(
    tmp_path, monkeypatch
):
    destination = tmp_path / "report.csv"
    destination.write_bytes(b"original CSV")

    def denied_replace(source, target):
        raise PermissionError("simulated Windows file lock")

    monkeypatch.setattr(os, "replace", denied_replace)
    with pytest.raises(PermissionError, match="Windows file lock"):
        atomic_write_text(destination, "new content")

    assert destination.read_bytes() == b"original CSV"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.csv"]


def test_missing_parent_is_not_created(tmp_path):
    destination = tmp_path / "missing" / "report.csv"
    with pytest.raises(FileNotFoundError):
        atomic_write_text(destination, "text")
    assert not destination.parent.exists()


def test_directory_destination_is_not_replaced(tmp_path):
    destination = tmp_path / "report.json"
    destination.mkdir()
    with pytest.raises(OSError):
        atomic_write_text(destination, "text")
    assert destination.is_dir()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.json"]


@pytest.mark.parametrize("command", ["json", "csv"])
def test_report_commands_keep_previous_file_on_replace_failure(
    command, tmp_path, monkeypatch, capsys
):
    destination = tmp_path / "report.txt"
    destination.write_bytes(b"previous output")
    monkeypatch.setattr(
        cli, "build_report", lambda *args, **kwargs: (object(), None, None, 0, 0)
    )
    monkeypatch.setattr(cli, "serialize", lambda report: {"ok": True})
    monkeypatch.setattr(cli, "to_csv", lambda report: "col\nvalue\n")

    def denied_replace(source, target):
        raise PermissionError("simulated locked report")

    monkeypatch.setattr(os, "replace", denied_replace)
    assert cli.main([command, "--offline", "--tz", "utc", "--out", str(destination)]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "output_failed" in output.err
    assert destination.read_bytes() == b"previous output"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.txt"]


def test_new_atomic_output_is_private_on_posix(tmp_path):
    destination = tmp_path / "new.csv"
    atomic_write_text(destination, "one,two\n")
    assert destination.read_text(encoding="utf-8") == "one,two\n"
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_symlink_destination_is_replaced_without_following_target(tmp_path):
    referent = tmp_path / "referent.txt"
    referent.write_text("previous referent", encoding="utf-8")
    destination = tmp_path / "linked-report.txt"
    try:
        destination.symlink_to(referent)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported by this platform")

    atomic_write_text(destination, "new file")

    assert destination.read_text(encoding="utf-8") == "new file"
    assert not destination.is_symlink()
    assert referent.read_text(encoding="utf-8") == "previous referent"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "linked-report.txt", "referent.txt"
    ]


def test_unwritable_parent_preserves_previous_output(tmp_path, monkeypatch):
    from vibewatt import atomic_output

    destination = tmp_path / "report.json"
    destination.write_bytes(b"old verified report")

    def denied_temp_creation(**kwargs):
        raise PermissionError("synthetic unwritable parent")

    monkeypatch.setattr(atomic_output.tempfile, "mkstemp", denied_temp_creation)
    with pytest.raises(PermissionError, match="unwritable parent"):
        atomic_write_text(destination, "new content")

    assert destination.read_bytes() == b"old verified report"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.json"]
