from datetime import datetime
from decimal import Decimal

import pandas as pd
import pytest

from app.analysis.sales import analyze_sales
from app.connectors.qy import SalesExtract
from app.schemas.sales import AnalysisWindow, DataScope


@pytest.fixture
def window():
    return AnalysisWindow(start="2026-09-01", end="2026-09-04")


@pytest.fixture
def scope():
    return DataScope(all_buyers=True)


@pytest.fixture
def source_as_of():
    return datetime.fromisoformat("2026-09-16T14:33:41+08:00")


@pytest.fixture
def extract():
    orders = pd.DataFrame(
        [
            [1, "DEMO-1", "BUYER-A", "2026-09-01", "订单完成", Decimal("100.0000")],
            [2, "DEMO-2", "BUYER-A", "2026-09-02", "订单完成", Decimal("200.0000")],
            [3, "DEMO-3", "BUYER-B", "2026-09-02", "已退回", Decimal("90.0000")],
        ],
        columns=["order_id", "order_number", "buyer_code", "created_at", "status", "amount"],
    )
    lines = pd.DataFrame(
        [
            [1, 1, "SKU-A", 2, Decimal("30.00"), Decimal("60.0000")],
            [1, 2, "SKU-B", 1, Decimal("40.00"), Decimal("40.0000")],
            [2, 1, "SKU-A", 1, Decimal("200.00"), Decimal("200.0000")],
            [3, 1, "SKU-B", 1, Decimal("90.00"), Decimal("90.0000")],
        ],
        columns=["order_id", "line_id", "product_code", "quantity", "unit_price", "amount"],
    )
    return SalesExtract(orders, lines, 3, Decimal("390.0000"), 0.1)


@pytest.fixture
def report(extract, window, scope, source_as_of):
    return analyze_sales(extract, window, scope, source_as_of=source_as_of, synthetic=True)
