"""从只读ERP源补充库存库龄、效期与成本，保留已有出库历史并保存新快照。"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from app.analysis.operations import validate_operations
from app.connectors.operations import read_inventory_risk
from app.connectors.qy import make_source_engine
from app.core.reports import load_report, save_report
from app.core.settings import source_odbc
from app.schemas.operations import OperationsSnapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出已存在，不允许覆盖")
    engine = None
    try:
        report = load_report(args.input)
        engine = make_source_engine(source_odbc())
        stock = read_inventory_risk(engine, report)
        data = report.operations.model_dump()
        data["inventory"] = stock
        report = report.model_copy(
            update={
                "operations": OperationsSnapshot.model_validate(data),
                "metadata": report.metadata.model_copy(
                    update={"generated_at": datetime.now(timezone.utc)}
                ),
            }
        )
        validate_operations(report)
        save_report(report, args.output)
        print(f"新快照已保存，核验库存记录{len(stock.records)}条。")
        return 0
    except Exception as exc:
        print(f"未发布快照（{type(exc).__name__}），原快照不变。")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
