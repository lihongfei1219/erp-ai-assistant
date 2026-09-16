import pytest

from app.core.reports import load_report, save_report


def test_round_trip_and_no_overwrite(report, tmp_path):
    path = tmp_path / "report.json"
    save_report(report, path)
    assert load_report(path) == report
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_report(report, path)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_invalid_snapshot_not_accepted(tmp_path):
    path = tmp_path / "report.json"
    path.write_text('{"metadata": {"schema_version": "999"}}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_report(path)
