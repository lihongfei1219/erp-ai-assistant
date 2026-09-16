import os
from datetime import datetime
from decimal import Decimal

import pytest

from app.analysis.sales import analyze_sales
from app.connectors.qy import SourceDataError, make_source_engine, read_sales
from app.core.settings import source_odbc
from app.schemas.sales import AnalysisWindow, DataScope

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ERP_RUN_INTEGRATION") != "1",
        reason="Set ERP_RUN_INTEGRATION=1 to opt into read-only local SQL Server checks",
    ),
]


@pytest.fixture
def engine():
    source = make_source_engine(source_odbc())
    yield source
    source.dispose()


def test_real_decimal_read_and_reconciliation(engine):
    window = AnalysisWindow(start="2026-09-01", end="2026-09-17")
    scope = DataScope(all_buyers=True)
    extract = read_sales(engine, window, scope)
    assert not extract.orders.empty
    assert all(isinstance(value, Decimal) for value in extract.orders.amount)
    result = analyze_sales(
        extract, window, scope, source_as_of=datetime.fromisoformat("2026-09-16T14:33:41+08:00")
    )
    assert result.quality.sql_control_totals_match
    assert result.quality.reconciled_orders == len(extract.orders)


def test_enterprise_scope_is_parameterized_and_applied(engine):
    window = AnalysisWindow(start="2026-09-01", end="2026-09-17")
    all_rows = read_sales(engine, window, DataScope(all_buyers=True))
    buyer = all_rows.orders.buyer_code.iloc[0]
    scoped = read_sales(engine, window, DataScope(buyer_codes=(buyer,)))
    assert set(scoped.orders.buyer_code) == {buyer}
    assert len(scoped.orders) < len(all_rows.orders)
    malicious = read_sales(engine, window, DataScope(buyer_codes=("' OR 1=1 --",)))
    assert malicious.orders.empty


def test_read_limits_do_not_publish_truncated_results(engine):
    with pytest.raises(SourceDataError, match="订单数超过"):
        read_sales(
            engine,
            AnalysisWindow(start="2026-09-01", end="2026-09-17"),
            DataScope(all_buyers=True),
            max_orders=1,
        )
