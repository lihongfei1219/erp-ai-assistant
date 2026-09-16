"""Development-only immutable snapshots. Production assistant DB remains planned."""

import os
import tempfile
from pathlib import Path

from app.schemas.sales import SalesReport

MAX_REPORT_BYTES = 64 * 1024 * 1024


def save_report(report: SalesReport, path: Path) -> None:
    payload = report.model_dump_json(indent=2).encode("utf-8")
    if len(payload) > MAX_REPORT_BYTES:
        raise ValueError("快照超过 64 MiB，请缩小分析范围")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic creation without replacing an existing report, including concurrent writers.
        os.link(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_report(path: Path) -> SalesReport:
    with path.open("rb") as handle:
        payload = handle.read(MAX_REPORT_BYTES + 1)
    if len(payload) > MAX_REPORT_BYTES:
        raise ValueError("快照超过读取上限")
    return SalesReport.model_validate_json(payload)
