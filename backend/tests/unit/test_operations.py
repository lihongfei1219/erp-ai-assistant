from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.analysis.analytics import execute_analysis
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisPlan
from app.schemas.sales import SalesReport


def plan(domain, kind="summary", metric="amount", **kw):
    start = "2026-09-16" if domain == "inventory" else "2026-09-01"
    end = "2026-09-17" if domain == "inventory" else "2026-09-04"
    return AnalysisPlan(
        steps=[
            dict(
                domain=domain,
                kind=kind,
                metric=metric,
                start_date=start,
                end_date_exclusive=end,
                **kw,
            )
        ]
    )


def test_returns_are_separate_facts_with_distinct_documents_and_units(multi_report):
    result = execute_analysis(multi_report, plan("returns")).results[0]
    assert result.domain == "returns"
    assert result.totals["amount"] == "35.0000"
    assert result.totals["document_count"] == 2
    assert "quantity" not in result.totals
    assert {r["unit"]: Decimal(r["quantity"]) for r in result.rows} == {
        "盒": Decimal("2.5"),
        "瓶": Decimal("1"),
    }
    assert "退款" in "".join(result.notes)
    assert "evidence_ids" not in result.rows[0]


def test_shipping_uses_event_date_and_preserves_source_ids(multi_report):
    result = execute_analysis(multi_report, plan("shipping", "trend")).results[0]
    by_day = {r["day"]: r for r in result.rows}
    assert by_day["2026-09-02"]["document_count"] == 0
    assert by_day["2026-09-03"]["document_count"] == 2
    assert "确认" in "".join(result.notes)


def test_inventory_merges_batches_but_never_compares_different_units(multi_report):
    result = execute_analysis(
        multi_report, plan("inventory", "product_ranking", "stock", top_n=1)
    ).results[0]
    assert {(r["code"], r["unit"], Decimal(r["quantity"])) for r in result.rows} == {
        ("A", "盒", Decimal("15")),
        ("B", "瓶", Decimal("100")),
    }
    assert all(r["rank"] == 1 for r in result.rows)
    assert "quantity" not in result.totals
    assert result.chart is None


def test_returns_product_order_ranking_deduplicates_document(multi_report):
    result = execute_analysis(multi_report, plan("returns", "product_ranking", "orders")).results[0]
    assert result.rows[0]["code"] == "A"
    assert result.rows[0]["document_count"] == 2


@pytest.mark.parametrize(
    "domain,kind,metric",
    [
        ("returns", "list", "quantity"),
        ("shipping", "existence", "orders"),
        ("inventory", "summary", "stock"),
    ],
)
def test_business_modes_execute(multi_report, domain, kind, metric):
    result = execute_analysis(multi_report, plan(domain, kind, metric)).results[0]
    assert result.domain == domain and result.rows


def test_missing_operations_are_not_zero(report):
    with pytest.raises(QueryUnavailable, match="未加载|未接入"):
        execute_analysis(report, plan("returns"))


@pytest.mark.parametrize("metric", ["amount", "orders", "quantity"])
def test_customer_list_discloses_truncation(multi_report, metric):
    data = multi_report.model_dump(mode="json")
    facts = data["operations"]["returns"]
    template = facts["documents"][1]
    documents = []
    for i in range(11):
        document = deepcopy(template)
        document.update(
            document_id=i + 1,
            document_number=f"R-{i}",
            buyer_code=f"BUYER-{i:02}",
            buyer_name=f"Customer {i}",
        )
        documents.append(document)
    facts.update(
        documents=documents,
        control_document_count=11,
        control_line_count=11,
        control_amount="55.0000",
        control_quantities={"盒": "5.5"},
    )
    result = execute_analysis(
        SalesReport.model_validate(data),
        plan("returns", "list", metric, dimension="buyer", top_n=10),
    ).results[0]
    assert len(result.rows) == 10
    assert result.totals["total_groups"] == 11
    assert result.totals["document_count"] == 11
    assert result.totals["amount"] == "55.0000"
    assert any("11" in note and "10" in note and "展示" in note for note in result.notes)


def test_inventory_historical_range_is_rejected(multi_report):
    with pytest.raises(QueryUnavailable, match="时点|快照"):
        execute_analysis(
            multi_report,
            plan("inventory", metric="stock").model_copy(
                update={
                    "steps": [
                        plan("inventory", metric="stock")
                        .steps[0]
                        .model_copy(
                            update={
                                "start_date": date(2026, 9, 1),
                                "end_date_exclusive": date(2026, 9, 2),
                            }
                        )
                    ]
                }
            ),
        )


@pytest.mark.parametrize(
    "fault", ["amount", "scope", "duplicate", "unit", "stock_scope", "stock_time"]
)
def test_corrupt_or_out_of_scope_facts_never_execute(multi_report, fault):
    data = multi_report.model_dump(mode="json")
    ops = data["operations"]
    if fault == "amount":
        ops["returns"]["documents"][0]["amount"] = "999"
    if fault == "scope":
        ops["all_buyers"] = False
        ops["buyer_codes"] = ["BUYER-B"]
    if fault == "duplicate":
        ops["returns"]["documents"][1]["document_id"] = 1
    if fault == "unit":
        ops["returns"]["documents"][0]["lines"][0]["unit"] = ""
    if fault == "stock_scope":
        data["metadata"]["scope"] = {"all_buyers": False, "buyer_codes": ["BUYER-A"]}
    if fault == "stock_time":
        ops["inventory"]["as_of"] = "2026-09-15T00:00:00+08:00"
    with pytest.raises((ValidationError, QueryUnavailable)):
        tampered = SalesReport.model_validate(data)
        execute_analysis(
            tampered,
            plan("inventory", metric="stock") if fault.startswith("stock") else plan("returns"),
        )
