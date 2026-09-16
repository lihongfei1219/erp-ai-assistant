import argparse
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.analysis.sales import analyze_sales
from app.connectors.qy import SourceDataError, make_source_engine, read_sales
from app.core.business_rules import load_business_rules
from app.core.reports import save_report
from app.core.settings import source_odbc
from app.schemas.sales import AnalysisWindow, DataScope


def main() -> int:
    parser = argparse.ArgumentParser(description="只读计算 QY 销售订单快照；不等同于支付成交")
    parser.add_argument("--start", required=True, help="起始日期，含当日")
    parser.add_argument("--end", required=True, help="结束日期，不含当日；最多 366 天")
    parser.add_argument("--source-as-of", required=True, help="来源备份截至时间，必须带时区")
    scope_args = parser.add_mutually_exclusive_group(required=True)
    scope_args.add_argument("--all-buyers", action="store_true", help="确认分析全部购货单位")
    scope_args.add_argument(
        "--buyer-code", action="append", default=[], help="限指定业务编码，可重复"
    )
    parser.add_argument("--output", type=Path, help="新快照文件路径，不允许覆盖已有文件")
    parser.add_argument(
        "--rules", type=Path, help="可选的业务规则 JSON，默认读取 config/business-rules.json"
    )
    args = parser.parse_args()
    engine = None
    try:
        window = AnalysisWindow(start=args.start, end=args.end)
        scope = DataScope(all_buyers=args.all_buyers, buyer_codes=tuple(args.buyer_code))
        source_as_of = datetime.fromisoformat(args.source_as_of)
        rules = load_business_rules(args.rules)
        engine = make_source_engine(source_odbc())
        extract = read_sales(engine, window, scope)
        report = analyze_sales(extract, window, scope, source_as_of=source_as_of, rules=rules)
        output = args.output or (
            Path(__file__).resolve().parents[2] / ".local" / "reports" / f"{uuid4().hex}.json"
        )
        save_report(report, output)
        # Do not print raw values, buyer codes or driver errors into terminal logs.
        print(f"Report saved: {output.resolve()}")
        print(
            f"Orders: {report.summary.order_count}; lines: {report.summary.line_count}; "
            f"SQL reconciliation: {report.quality.sql_control_totals_match}"
        )
        return 0
    except SourceDataError as exc:
        print(f"分析未发布：{exc}。已有快照保持不变。", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"分析未发布（{type(exc).__name__}）。请检查连接、参数和数据质量；已有快照保持不变。",
            file=sys.stderr,
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
