"""Verified dispatch purchase costs, never substituted with current inventory prices."""

from datetime import timedelta
from decimal import Decimal

from app.analysis.growth import LABELS, coverage, partitions, period_covered, validate_growth
from app.analysis.sales_query import QueryUnavailable


def cost_coverage(report):
    valid = {(p.start, p.end_exclusive) for p in partitions(report) if p.cost_ready}
    return [
        (a, b) for a, b in coverage(report) if any(start == a and b <= end for start, end in valid)
    ]


def validate_margin(report, step):
    periods = validate_growth(report, step)
    intervals = cost_coverage(report)
    for key, (start, end) in periods.items():
        if not period_covered(intervals, start, end):
            raise QueryUnavailable(
                f"{LABELS[key]} {start} 至 {end - timedelta(days=1)} "
                "缺少已核验的出库采购成本，请生成带成本的新快照。"
            )
    return periods


def group_cost(group, covered):
    if not covered or group and not group["cost_known"]:
        return None
    return group["cost"] if group else Decimal(0)
