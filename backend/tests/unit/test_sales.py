from datetime import datetime
from decimal import Decimal

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from pydantic import ValidationError

from app.analysis.sales import analyze_sales
from app.connectors.qy import SourceDataError
from app.schemas.sales import AnalysisWindow, DataScope


def test_correct_grain_and_raw_status_policy(report):
    assert report.summary.order_amount == Decimal("390.0000")
    assert report.summary.order_count == 3
    assert report.summary.buyer_count == 2
    assert report.summary.average_order_amount == Decimal("130.0000")
    assert report.summary.completed_status_order_count == 2
    assert report.summary.completed_status_order_amount == Decimal("300.0000")
    assert report.summary.line_count == 4
    assert report.summary.product_count == 2
    assert report.buyers[0].order_amount == Decimal("300.0000")
    assert report.buyers[0].order_count == 2
    assert report.products[0].order_amount == Decimal("260.0000")
    assert report.products[0].order_count == 2
    assert report.daily[2].order_count == 0
    # A buyer appears on both days: daily unique counts must not be summed for the period.
    assert sum(day.buyer_count for day in report.daily) == 3
    assert len(report.evidence[0].lines) == 2


def test_input_is_not_mutated(extract, window, scope, source_as_of):
    before_orders, before_lines = extract.orders.copy(deep=True), extract.lines.copy(deep=True)
    analyze_sales(extract, window, scope, source_as_of=source_as_of)
    assert_frame_equal(extract.orders, before_orders)
    assert_frame_equal(extract.lines, before_lines)


def test_empty_range_is_zero_with_no_invented_average(extract, window, scope, source_as_of):
    extract.orders = extract.orders.iloc[:0]
    extract.lines = extract.lines.iloc[:0]
    extract.sql_order_count, extract.sql_order_amount = 0, Decimal("0")
    result = analyze_sales(extract, window, scope, source_as_of=source_as_of)
    assert result.summary.order_amount == 0
    assert result.summary.average_order_amount is None
    assert result.buyers == result.products == result.evidence == []
    assert len(result.daily) == 3


@pytest.mark.parametrize("target", ["orders", "lines"])
def test_duplicate_keys_fail_closed(extract, window, scope, source_as_of, target):
    frame = getattr(extract, target)
    setattr(extract, target, pd.concat([frame, frame.iloc[:1]], ignore_index=True))
    with pytest.raises(SourceDataError, match="重复主键"):
        analyze_sales(extract, window, scope, source_as_of=source_as_of)


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", None),
        ("buyer_code", ""),
        ("order_id", None),
        ("status", ""),
    ],
)
def test_missing_values_fail_closed(extract, window, scope, source_as_of, field, value):
    extract.orders[field] = extract.orders[field].astype(object)
    extract.orders.loc[0, field] = value
    with pytest.raises(SourceDataError):
        analyze_sales(extract, window, scope, source_as_of=source_as_of)


def test_missing_columns_fail_closed(extract, window, scope, source_as_of):
    extract.lines = extract.lines.drop(columns="amount")
    with pytest.raises(SourceDataError, match="缺少必需字段"):
        analyze_sales(extract, window, scope, source_as_of=source_as_of)


def test_orphan_lines_fail_closed(extract, window, scope, source_as_of):
    extract.lines.loc[0, "order_id"] = 999
    with pytest.raises(SourceDataError, match="没有对应表头"):
        analyze_sales(extract, window, scope, source_as_of=source_as_of)


def test_order_without_lines_fails_closed(extract, window, scope, source_as_of):
    extract.lines = extract.lines.loc[extract.lines.order_id != 3]
    with pytest.raises(SourceDataError, match="没有明细"):
        analyze_sales(extract, window, scope, source_as_of=source_as_of)


def test_amount_mismatch_blocks_publication(extract, window, scope, source_as_of):
    extract.lines.loc[0, "amount"] = Decimal("60.0001")
    with pytest.raises(SourceDataError, match="表头金额与明细"):
        analyze_sales(extract, window, scope, source_as_of=source_as_of)


def test_unit_price_is_not_used_to_overwrite_erp_amount(extract, window, scope, source_as_of):
    extract.lines.loc[0, "unit_price"] = Decimal("29.99")
    result = analyze_sales(extract, window, scope, source_as_of=source_as_of)
    assert result.summary.order_amount == Decimal("390.0000")
    assert result.quality.price_quantity_mismatch_count == 1


def test_float_amount_rejected(extract, window, scope, source_as_of):
    extract.orders.loc[0, "amount"] = 100.0
    with pytest.raises(SourceDataError, match="浮点"):
        analyze_sales(extract, window, scope, source_as_of=source_as_of)


def test_decimal_four_places_preserved(extract, window, scope, source_as_of):
    extract.orders.loc[0, "amount"] += Decimal("0.0001")
    extract.lines.loc[0, "amount"] += Decimal("0.0001")
    extract.sql_order_amount += Decimal("0.0001")
    result = analyze_sales(extract, window, scope, source_as_of=source_as_of)
    assert result.summary.order_amount == Decimal("390.0001")
    assert '"order_amount":"390.0001"' in result.model_dump_json()


@pytest.mark.parametrize(
    "field,value", [("sql_order_count", 4), ("sql_order_amount", Decimal("391"))]
)
def test_independent_control_totals_checked(extract, window, scope, source_as_of, field, value):
    setattr(extract, field, value)
    with pytest.raises(SourceDataError, match="SQL 控制"):
        analyze_sales(extract, window, scope, source_as_of=source_as_of)


def test_scope_is_revalidated_after_read(extract, window, source_as_of):
    with pytest.raises(SourceDataError, match="授权企业"):
        analyze_sales(
            extract, window, DataScope(buyer_codes=("BUYER-A",)), source_as_of=source_as_of
        )


@pytest.mark.parametrize("bad_date", ["2026-08-31", "2026-09-04", "not-a-date"])
def test_dates_are_validated(extract, window, scope, source_as_of, bad_date):
    extract.orders.loc[0, "created_at"] = bad_date
    with pytest.raises(SourceDataError):
        analyze_sales(extract, window, scope, source_as_of=source_as_of)


def test_future_days_not_filled_with_zero(extract, scope):
    result = analyze_sales(
        extract,
        AnalysisWindow(start="2026-09-01", end="2026-09-07"),
        scope,
        source_as_of=datetime.fromisoformat("2026-09-02T12:00:00+08:00"),
    )
    assert len(result.daily) == 2
    assert any("备份当日" in warning for warning in result.metadata.warnings)


def test_future_source_time_rejected(extract, window, scope):
    with pytest.raises(SourceDataError, match="不能晚于"):
        analyze_sales(
            extract,
            window,
            scope,
            source_as_of=datetime.fromisoformat("2026-09-05T00:00:00+08:00"),
            generated_at=datetime.fromisoformat("2026-09-04T00:00:00+08:00"),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"all_buyers": True, "buyer_codes": ("A",)},
        {"buyer_codes": ("",)},
        {"buyer_codes": ("A", "A")},
    ],
)
def test_scope_must_be_explicit_and_unambiguous(kwargs):
    with pytest.raises(ValidationError):
        DataScope(**kwargs)


@pytest.mark.parametrize(
    "start,end",
    [("2026-01-01", "2026-01-01"), ("2026-02-01", "2026-01-01"), ("2024-01-01", "2026-01-01")],
)
def test_bounded_query_window(start, end):
    with pytest.raises(ValidationError):
        AnalysisWindow(start=start, end=end)
