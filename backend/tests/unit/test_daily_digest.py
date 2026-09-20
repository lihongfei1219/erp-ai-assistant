from datetime import date, datetime
from decimal import Decimal

import pytest

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.notifications.daily import DigestUnavailable, build_digest
from app.schemas.sales import AnalysisWindow


def make_report(extract, window, scope, source_as_of):
    return analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )


def test_partial_day_allowed_only_for_labelled_demo(extract, scope):
    report = make_report(
        extract,
        AnalysisWindow(start="2026-09-01", end="2026-09-04"),
        scope,
        datetime.fromisoformat("2026-09-02T14:33:00+08:00"),
    )
    with pytest.raises(DigestUnavailable):
        build_digest(report, date(2026, 9, 2))
    digest = build_digest(report, date(2026, 9, 2), demo=True)
    assert digest.partial is True
    assert digest.amount_change_percent is None
    assert digest.previous_summary.order_amount == Decimal("100")
    assert "不完整" in digest.text
    assert "演示" in digest.title
    assert "14:33" in digest.text


def test_empty_day_is_zero_only_inside_observed_range(extract, window, scope, source_as_of):
    report = make_report(extract, window, scope, source_as_of)
    digest = build_digest(report, date(2026, 9, 3))
    assert digest.summary.order_count == 0
    assert digest.summary.average_order_amount is None
    assert digest.amount_change_percent == Decimal("-100.00")
    assert not digest.buyers
    with pytest.raises(DigestUnavailable):
        build_digest(report, date(2026, 9, 19), demo=True)


def test_zero_baseline_and_missing_previous_day_are_not_growth(
    extract,
    window,
    scope,
    source_as_of,
):
    report = make_report(extract, window, scope, source_as_of)
    first = build_digest(report, date(2026, 9, 1))
    assert first.previous_summary is None
    assert first.amount_change_percent is None
    extract.orders.loc[0, "status"] = "已退回"
    report = make_report(extract, window, scope, source_as_of)
    second = build_digest(report, date(2026, 9, 2))
    assert second.previous_summary.order_count == 0
    assert second.amount_change_percent is None


def test_legacy_snapshot_cannot_guess_effective_policy(report):
    with pytest.raises(DigestUnavailable):
        build_digest(report, date(2026, 9, 2), demo=True)
