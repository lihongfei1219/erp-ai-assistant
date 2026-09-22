"""Append reconciled business facts to a NEW immutable snapshot, retaining sales evidence."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from app.analysis.operations import validate_operations
from app.connectors.operations import read_operations
from app.connectors.qy import make_source_engine
from app.core.reports import load_report, save_report
from app.core.settings import source_odbc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出文件已存在，不允许覆盖快照")
    report = load_report(args.input)
    engine = make_source_engine(source_odbc())
    try:
        facts = read_operations(engine, report)
        report = report.model_copy(
            update={
                "operations": facts,
                "metadata": report.metadata.model_copy(
                    update={"generated_at": datetime.now(timezone.utc)}
                ),
            }
        )
        validate_operations(report)
        save_report(report, args.output)
        print("New snapshot saved; returns, sales dispatch and authorized inventory reconciled.")
        return 0
    except Exception as exc:
        print(f"Snapshot not published ({type(exc).__name__}); original snapshot unchanged.")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
