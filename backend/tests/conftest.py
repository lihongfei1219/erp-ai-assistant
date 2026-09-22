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


def operations_payload(report):
    from copy import deepcopy

    def line(i, code, unit, quantity, amount):
        return dict(
            line_id=i,
            product_code=code,
            product_name=code,
            unit=unit,
            quantity=quantity,
            amount=amount,
        )

    def document(i, buyer, amount, lines):
        return dict(
            document_id=i,
            document_number=f"DEMO-{i}",
            buyer_code=buyer,
            buyer_name=buyer,
            original_order_id=i,
            original_order_number=f"SALE-{i}",
            occurred_at="2026-09-02T12:00:00+08:00",
            status="订单完成",
            amount=amount,
            lines=lines,
        )

    documents = [
        document(
            1, "BUYER-A", "30.0000", [line(1, "A", "盒", "2", "20"), line(2, "B", "瓶", "1", "10")]
        ),
        document(2, "BUYER-A", "5.0000", [line(1, "A", "盒", "0.5", "5")]),
    ]
    facts = dict(
        start="2026-09-01",
        end_exclusive="2026-09-04",
        time_basis="退货单创建日期",
        source_tables=["XSTHDH", "XSTHDB"],
        included_statuses=["订单完成"],
        excluded_document_count=1,
        documents=documents,
        control_document_count=2,
        control_line_count=3,
        control_amount="35.0000",
        control_quantities={"盒": "2.5", "瓶": "1"},
    )
    shipping = deepcopy(facts)
    shipping.update(
        time_basis="销售出库确认日期",
        source_tables=["CKFHQRH", "CKFHQRB"],
        included_statuses=["已确认"],
        excluded_document_count=0,
    )
    for doc in shipping["documents"]:
        doc["status"] = "已确认"
        doc["occurred_at"] = "2026-09-03T09:00:00+08:00"
    stock = [
        dict(
            record_id=1,
            product_code="A",
            product_name="A",
            unit="盒",
            batch_code="B1",
            quantity="10",
        ),
        dict(
            record_id=2,
            product_code="A",
            product_name="A",
            unit="盒",
            batch_code="B2",
            quantity="5",
        ),
        dict(
            record_id=3,
            product_code="B",
            product_name="B",
            unit="瓶",
            batch_code="B3",
            quantity="100",
        ),
    ]
    return dict(
        source_as_of=report.metadata.source_as_of.isoformat(),
        all_buyers=True,
        buyer_codes=[],
        returns=facts,
        shipping=shipping,
        inventory=dict(
            as_of=report.metadata.source_as_of.isoformat(),
            records=stock,
            control_record_count=3,
            control_quantities={"盒": "15", "瓶": "100"},
        ),
    )


@pytest.fixture
def multi_report(extract, window, scope, source_as_of):
    from app.core.business_rules import load_business_rules
    from app.schemas.sales import SalesReport

    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    data = report.model_dump(mode="json")
    data["operations"] = operations_payload(report)
    return SalesReport.model_validate(data)
