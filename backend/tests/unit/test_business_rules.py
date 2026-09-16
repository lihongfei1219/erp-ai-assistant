from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.analysis.sales import analyze_sales
from app.connectors.qy import SourceDataError
from app.core.business_rules import load_business_rules, policy_fingerprint
from app.core.reports import load_report, save_report
from app.schemas.sales import BusinessRules


def test_default_rules_keep_raw_and_effective_totals_separate(extract, window, scope, source_as_of):
    result = analyze_sales(
        extract, window, scope, source_as_of=source_as_of, rules=load_business_rules()
    )
    assert result.summary.order_amount == Decimal("390")
    assert result.operating.summary.order_amount == Decimal("300")
    assert result.operating.summary.order_count == 2
    assert result.operating.summary.buyer_count == 1
    assert result.operating.summary.line_count == 3
    assert result.operating.excluded_order_amount == Decimal("90")
    assert result.operating.excluded_order_count == 1
    assert result.operating.products[1].order_amount == Decimal("40")
    assert result.operating.buyers[0].order_amount == Decimal("300")
    assert result.operating.daily[1].order_amount == Decimal("200")
    assert result.operating.unknown_statuses == []


def test_custom_states_recompute_all_views_consistently(extract, window, scope, source_as_of):
    rules = load_business_rules().model_dump()
    rules.update(policy_id="qy.operating.v2", included_statuses=["已退回"])
    result = analyze_sales(
        extract, window, scope, source_as_of=source_as_of, rules=BusinessRules.model_validate(rules)
    )
    assert result.operating.summary.order_amount == Decimal("90")
    assert result.operating.summary.order_count == 1
    assert result.operating.buyers[0].code == "BUYER-B"
    assert [item.code for item in result.operating.products] == ["SKU-B"]
    assert result.operating.daily[0].order_amount == 0
    assert result.operating.daily[1].order_amount == 90


def test_unknown_state_is_reported_and_excluded(extract, window, scope, source_as_of):
    extract.orders.loc[0, "status"] = "未来新增状态"
    result = analyze_sales(
        extract, window, scope, source_as_of=source_as_of, rules=load_business_rules()
    )
    assert result.operating.summary.order_amount == Decimal("200")
    assert result.operating.unknown_statuses[0].status == "未来新增状态"
    assert result.operating.unknown_statuses[0].order_count == 1


def test_no_effective_orders_is_empty_not_failure(extract, window, scope, source_as_of):
    extract.orders["status"] = "待审核"
    result = analyze_sales(
        extract, window, scope, source_as_of=source_as_of, rules=load_business_rules()
    )
    assert result.operating.summary.order_count == 0
    assert result.operating.summary.average_order_amount is None
    assert result.operating.buyers == []
    assert result.operating.products == []
    assert all(item.order_amount == 0 for item in result.operating.daily)


@pytest.mark.parametrize(
    "changes",
    [
        {"included_statuses": []},
        {"included_statuses": ["不存在"]},
        {"known_statuses": ["订单完成", "订单完成"]},
        {"included_statuses": [" 订单完成"]},
        {"business_timezone": "Fake/Zone"},
        {"currency": "人民币"},
        {"refund_policy": "deduct_as_cash"},
    ],
)
def test_invalid_business_rules_rejected(changes):
    fields = load_business_rules().model_dump()
    fields.update(changes)
    with pytest.raises(ValidationError):
        BusinessRules.model_validate(fields)


def test_rule_fingerprint_tracks_content_even_if_id_unchanged():
    original = load_business_rules()
    assert policy_fingerprint(original) == policy_fingerprint(load_business_rules())
    changed = original.model_copy(update={"included_statuses": ("订单完成",)})
    assert policy_fingerprint(changed) != policy_fingerprint(original)


def test_snapshot_keeps_original_rules(extract, window, scope, source_as_of, tmp_path):
    rules = load_business_rules()
    result = analyze_sales(extract, window, scope, source_as_of=source_as_of, rules=rules)
    file = tmp_path / "rules-snapshot.json"
    save_report(result, file)
    changed = rules.model_copy(update={"included_statuses": ("待审核",)})
    restored = load_report(file)
    assert restored.operating.policy != changed
    assert restored.operating.policy == rules
    assert restored.operating.policy_fingerprint == policy_fingerprint(rules)


def test_optional_names_do_not_change_business_identity(extract, window, scope, source_as_of):
    extract.orders["buyer_name"] = ["客户旧名称", "客户新名称", None]
    extract.lines["product_name"] = ["商品甲", "商品乙", "商品甲", None]
    result = analyze_sales(
        extract, window, scope, source_as_of=source_as_of, rules=load_business_rules()
    )
    assert result.summary.buyer_count == 2
    assert result.operating.buyers[0].name == "客户新名称"
    assert result.evidence[0].buyer_name == "客户旧名称"
    assert result.evidence[0].lines[0].product_name == "商品甲"


def test_excluded_orders_still_need_to_reconcile(extract, window, scope, source_as_of):
    extract.orders.loc[2, "amount"] = Decimal("999")
    with pytest.raises(SourceDataError, match="表头金额与明细"):
        analyze_sales(
            extract, window, scope, source_as_of=source_as_of, rules=load_business_rules()
        )
