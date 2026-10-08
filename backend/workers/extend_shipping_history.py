"""只读提取历史销售出库，按月对账并保存新的不可覆盖快照。"""

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.analysis.operations import validate_operations
from app.connectors.operations import read_shipping_history
from app.connectors.qy import make_source_engine
from app.core.reports import load_report, save_report
from app.core.settings import source_odbc
from app.schemas.operations import OperationsSnapshot
from app.schemas.sales import AnalysisWindow, SalesReport


def history_windows(start, end, primary):
    if not start < end or (end - start).days > 1096:
        raise ValueError("历史范围须为1天至3年")
    points = {start, end}
    cursor = start.replace(day=1)
    while cursor < end:
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        if cursor < end:
            points.add(cursor)
    for point in (primary.start, primary.end_exclusive):
        if start < point < end:
            points.add(point)
    points = sorted(points)
    return [
        AnalysisWindow(start=a, end=b)
        for a, b in zip(points, points[1:], strict=False)
        if b <= primary.start or a >= primary.end_exclusive
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出已存在，不能覆盖")
    engine = None
    try:
        report = load_report(args.input)
        primary = report.operations.shipping
        windows = history_windows(args.start, args.end, primary)
        # Re-extract primary identity; queries still exclude its partial final day.
        engine = make_source_engine(source_odbc())
        parts = read_shipping_history(
            engine,
            report,
            [AnalysisWindow(start=primary.start, end=primary.end_exclusive), *windows],
        )
        data = report.operations.model_dump()
        data["shipping"] = parts[0]
        data["shipping_history"] = parts[1:]
        facts = OperationsSnapshot.model_validate(data)
        report = SalesReport.model_validate(
            report.model_copy(
                update={
                    "operations": facts,
                    "metadata": report.metadata.model_copy(
                        update={"generated_at": datetime.now(timezone.utc)}
                    ),
                }
            ).model_dump()
        )
        validate_operations(report)
        save_report(report, args.output)
        count = sum(p.control_document_count for p in parts)
        print(f"已保存新快照：分区{len(parts)}段，出库单{count}张。")
        return 0
    except Exception as exc:
        print(f"未发布快照（{type(exc).__name__}），原快照保持不变。")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
