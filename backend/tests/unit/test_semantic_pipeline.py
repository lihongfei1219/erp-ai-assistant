"""Business contract tests: provider output is untrusted, execution stays deterministic."""

from datetime import date

import pytest


def compiler():
    # Import here so the initial red run reports the missing feature per behavior.
    from app.semantic.compiler import compile_request
    from app.semantic.schemas import SemanticRequest

    def run(intents, question, previous=None, mode="new", **extra):
        return compile_request(
            SemanticRequest(intents=intents, mode=mode, **extra),
            question=question,
            today=date(2026, 9, 22),
            previous=previous,
        )

    return run


def test_chinese_single_day_compiles_to_existing_sales_plan():
    result = compiler()(
        [dict(domain="sales", operation="ranking", target="product", time="九月十五号", limit=1)],
        "九月十五号哪个药品卖得最好",
    )
    step = result.plan.steps[0]
    assert (str(step.start_date), str(step.end_date_exclusive)) == ("2026-09-15", "2026-09-16")
    assert (step.kind, step.metric, step.top_n) == ("product_ranking", "amount", 1)


@pytest.mark.parametrize(
    "question,time",
    [
        ("九月五号那天大家最爱买啥，给我看看", "九月五号"),
        ("9月15号帮我捋捋，咱们卖出去的钱都在哪些货上", "9月15号"),
        ("9月15号咱家商品销售额排行", "9月15号"),
    ],
)
def test_model_understood_colloquial_ranking_is_not_reclassified_by_question_text(question, time):
    result = compiler()(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="product",
                metric="amount",
                time=time,
                limit=10,
            )
        ],
        question,
    )
    assert result.status == "ready"
    assert (result.plan.steps[0].kind, result.plan.steps[0].top_n) == ("product_ranking", 10)


@pytest.mark.parametrize("buyer_limit", [None, 10])
def test_each_ranking_limit_comes_from_its_structured_intent(buyer_limit):
    result = compiler()(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="product",
                metric="amount",
                time="9月15号",
                limit=1,
            ),
            dict(
                domain="sales",
                operation="ranking",
                target="buyer",
                metric="amount",
                time="9月15号",
                limit=buyer_limit,
            ),
        ],
        "9月15号哪个商品卖得最好，再看客户排行",
    )
    assert result.status == "ready"
    assert [step.top_n for step in result.plan.steps] == [1, 10]


@pytest.mark.parametrize(
    "domain,word", [("returns", "退货"), ("shipping", "出库"), ("inventory", "库存")]
)
def test_other_domains_are_understood_but_never_executed_as_sales(domain, word):
    result = compiler()([dict(domain=domain, operation="summary", time="今天")], f"今天{word}多少")
    assert result.plan is None and result.status == "unsupported"
    assert word in result.message and "未接入" in result.message
    assert result.context.intents[0].domain == domain


def test_pending_time_is_completed_without_losing_original_task():
    run = compiler()
    pending = run([dict(domain="sales", operation="summary")], "这几天卖了多少钱")
    assert pending.status == "clarify" and "日期" in pending.message
    completed = run(
        [dict(time="九月十五号")], "九月十五号", previous=pending.context, mode="answer"
    )
    assert completed.status == "ready"
    assert completed.plan.steps[0].kind == "summary"
    assert str(completed.plan.steps[0].start_date) == "2026-09-15"


def test_combined_domains_do_not_silently_execute_sales_subset():
    result = compiler()(
        [
            dict(domain="sales", operation="summary", time="九月十五号"),
            dict(domain="returns", operation="summary", time="九月十五号"),
        ],
        "九月十五号销售和退货情况",
    )
    assert result.plan is None and result.status == "unsupported"


@pytest.mark.parametrize(
    "question,intent",
    [
        (
            "九月十五号药品销量前五名",
            dict(
                domain="sales",
                operation="ranking",
                target="product",
                metric="quantity",
                time="九月十五号",
                limit=5,
            ),
        ),
        (
            "九月十五号只看药品排除器械的销售",
            dict(
                domain="sales",
                operation="summary",
                metric="amount",
                time="九月十五号",
                filters=[dict(field="category", operator="exclude", value="器械")],
            ),
        ),
        (
            "九月十五号退货多少",
            dict(domain="returns", operation="summary", metric="amount", time="九月十五号"),
        ),
    ],
)
def test_structured_unsupported_quantity_filters_and_domains_do_not_execute(question, intent):
    result = compiler()([intent], question)
    assert result.plan is None and result.status == "unsupported"
    preserved = result.context.intents[0]
    assert preserved.domain == intent["domain"]
    assert preserved.metric == intent["metric"]
    assert [value.model_dump(exclude={"id"}) for value in preserved.filters] == intent.get(
        "filters", []
    )


def test_today_keeps_real_date_instead_of_snapshot_date():
    result = compiler()([dict(domain="sales", operation="summary", time="今天")], "今天卖了多少钱")
    assert str(result.plan.steps[0].start_date) == "2026-09-22"


def test_followup_preserves_metric_but_new_domain_does_not_inherit_it():
    run = compiler()
    initial = run(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="product",
                metric="orders",
                time="9月15号",
                limit=5,
            )
        ],
        "9月15号商品按订单数排前5名",
    )
    followup = run([dict(limit=3)], "取前3名", previous=initial.context, mode="followup")
    assert followup.plan.steps[0].metric == "orders"
    assert followup.plan.steps[0].top_n == 3
    switched = run(
        [dict(domain="inventory", operation="ranking", target="product")],
        "再看库存多的商品",
        previous=initial.context,
        mode="followup",
    )
    assert switched.context.intents[0].metric != "orders"
    assert switched.plan is None


def test_composite_sales_request_keeps_both_steps():
    result = compiler()(
        [
            dict(domain="sales", operation="trend", time="9月1日至5号"),
            dict(
                domain="sales", operation="ranking", target="product", time="9月1日至5号", limit=5
            ),
        ],
        "9月1日至5号每日销售趋势和商品排行前5名",
    )
    assert [s.kind for s in result.plan.steps] == ["trend", "product_ranking"]


def test_context_filters_survive_followup_and_still_block_unsupported_execution():
    run = compiler()
    initial = run(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="product",
                time="9月15号",
                filters=[dict(field="category", operator="exclude", value="器械")],
            )
        ],
        "9月15号排除器械的商品销售排行",
    )
    followup = run([dict(limit=3)], "取前3名", previous=initial.context, mode="followup")
    assert followup.plan is None and followup.context.intents[0].filters


def test_changing_operation_keeps_same_domain_filters():
    run = compiler()
    initial = run(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="product",
                time="9月15号",
                filters=[dict(field="category", operator="include", value="药品")],
            )
        ],
        "9月15号只看药品排行",
    )
    changed = run(
        [dict(operation="summary")], "改成销售概览", previous=initial.context, mode="followup"
    )
    assert changed.plan is None and changed.context.intents[0].filters


def test_changing_operation_keeps_structured_all_buyer_scope():
    run = compiler()
    initial = run(
        [
            dict(
                domain="sales",
                operation="summary",
                metric="amount",
                time="9月15号",
                scope="all_buyers",
            )
        ],
        "9月15号整个盘子的销售情况",
    )
    changed = run(
        [dict(operation="trend")], "再看变化曲线", previous=initial.context, mode="followup"
    )
    assert changed.plan is not None and changed.plan.steps[0].kind == "trend"
    assert changed.context.intents[0].scope == "all_buyers"


def test_narrowing_combined_goals_keeps_their_shared_all_buyer_scope():
    run = compiler()
    initial = run(
        [
            dict(
                domain="sales",
                operation="ranking",
                target=target,
                metric="amount",
                time="9月15号",
                scope="all_buyers",
            )
            for target in ("product", "buyer")
        ],
        "9月15号整个盘子的商品和客户排行",
    )
    changed = run(
        [],
        "合起来看看总体情况",
        previous=initial.context,
        mode="followup",
        edits=[
            dict(
                intent_id=initial.context.intents[0].id,
                operation="set",
                field="operation",
                value="summary",
            ),
            dict(intent_id=initial.context.intents[1].id, operation="remove_intent"),
        ],
    )
    assert changed.plan is not None and changed.plan.steps[0].kind == "summary"
    assert changed.context.intents[0].scope == "all_buyers"


def test_narrowing_combined_goals_with_conflicting_scopes_requires_clarification():
    run = compiler()
    initial = run(
        [
            dict(
                domain="sales",
                operation="ranking",
                target=target,
                metric="amount",
                time="9月15号",
                scope=scope,
            )
            for target, scope in (("product", "authorized"), ("buyer", "all_buyers"))
        ],
        "9月15号我负责的商品排行，再看整个盘子的客户排行",
    )
    changed = run(
        [dict(domain="sales", operation="summary")],
        "合起来看看总体情况",
        previous=initial.context,
        mode="followup",
    )
    assert changed.plan is None and changed.status == "clarify"


def test_generic_product_scope_note_follows_the_same_task_without_leaking_to_new_tasks():
    run = compiler()
    initial = run(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="product",
                metric="amount",
                time="9月15号",
                generic_product_scope=True,
            )
        ],
        "9月15号哪些药品卖得好",
    )
    assert initial.context.product_scope_note is True
    followup = run([dict(limit=3)], "看前三个", previous=initial.context, mode="followup")
    assert followup.plan is not None
    assert followup.context.intents[0].generic_product_scope is True
    assert followup.context.product_scope_note is True

    switched = run(
        [dict(domain="inventory", operation="ranking", target="product")],
        "再看积压最严重的货",
        previous=followup.context,
        mode="followup",
    )
    assert switched.status == "unsupported"
    assert switched.context.product_scope_note is False
    assert switched.context.intents[0].generic_product_scope is None

    new_request = run(
        [dict(domain="sales", operation="summary", time="9月15号")],
        "9月15号总共卖了多少钱",
        previous=followup.context,
        mode="new",
    )
    assert new_request.plan is not None
    assert new_request.context.product_scope_note is False
    assert new_request.context.intents[0].generic_product_scope is None


def test_changing_same_domain_operation_keeps_generic_product_scope_note():
    run = compiler()
    initial = run(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="product",
                metric="amount",
                time="9月15号",
                generic_product_scope=True,
            )
        ],
        "9月15号哪些药品卖得好",
    )
    changed = run(
        [dict(operation="trend")], "再看变化曲线", previous=initial.context, mode="followup"
    )
    assert changed.plan is not None and changed.plan.steps[0].kind == "trend"
    assert changed.context.intents[0].generic_product_scope is True
    assert changed.context.product_scope_note is True


@pytest.mark.parametrize(
    "question,intent",
    [
        ("十五号销售额", dict(domain="sales", operation="summary", time="9月14号")),
        ("这几天卖了多少钱", dict(domain="sales", operation="summary", time="9月15号")),
    ],
)
def test_model_cannot_invent_dates(question, intent):
    assert compiler()([intent], question).plan is None


def test_structured_target_metric_and_limit_reach_the_existing_executor():
    result = compiler()(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="buyer",
                metric="orders",
                time="9月15号",
                limit=5,
            )
        ],
        "九月十五号客户按订单数排行前五名",
    )
    step = result.plan.steps[0]
    assert (step.kind, step.metric, step.top_n) == ("buyer_ranking", "orders", 5)


def test_unresolved_combined_goal_is_not_silently_discarded():
    result = compiler()(
        [
            dict(
                domain="sales", operation="trend", time="9月15号", unresolved=["排行按商品还是客户"]
            )
        ],
        "9月15号销售趋势和排行",
    )
    assert result.plan is None and result.status == "clarify"
    assert result.context.intents[0].unresolved == ["排行按商品还是客户"]


def test_request_level_unresolved_is_preserved_and_can_be_explicitly_resolved():
    run = compiler()
    pending = run(
        [dict(domain="sales", operation="ranking", target="product", time="9月15号")],
        "9月15号商品排行",
        unresolved=["按金额还是订单数"],
    )
    repeated = run([dict(limit=3)], "取前三", previous=pending.context, mode="followup")
    assert repeated.plan is None
    resolved = run(
        [dict(metric="amount")],
        "按金额",
        previous=pending.context,
        mode="answer",
        resolved_conditions=["按金额还是订单数"],
    )
    assert resolved.plan is not None and resolved.plan.steps[0].metric == "amount"


@pytest.mark.parametrize(
    "question",
    [
        "今天一共卖了多少钱",
        "帮我看一下今天卖了多少钱",
        "今天我们总共卖了多少钱",
    ],
)
def test_polite_colloquial_phrases_do_not_look_like_entity_filters(question):
    result = compiler()([dict(domain="sales", operation="summary", time="今天")], question)
    assert result.plan is not None


def test_structured_descending_preserves_named_filter_for_local_binding():
    intent = dict(
        domain="sales",
        operation="ranking",
        target="product",
        metric="amount",
        time="9月15号",
        order="descending",
    )
    assert compiler()([intent], "9月15号商品按销售额倒序排名").plan is not None
    intent["filters"] = [dict(field="product", operator="include", value="阿莫西林")]
    filtered = compiler()([intent], "9月15号阿莫西林销售额")
    assert filtered.plan is not None and filtered.status == "ready"
    assert filtered.plan.steps[0].filters[0].code == "阿莫西林"
    assert filtered.context.intents[0].filters[0].value == "阿莫西林"


def test_unsupported_filter_cannot_disappear_on_a_short_followup():
    run = compiler()
    rejected = run(
        [
            dict(
                domain="sales",
                operation="summary",
                time="9月15号",
                filters=[dict(field="category", operator="exclude", value="器械")],
            )
        ],
        "9月15号只看药品排除器械的销售",
    )
    retried = run([dict()], "再看一下", previous=rejected.context, mode="followup")
    assert retried.plan is None and retried.context.intents[0].filters


def test_pending_relative_date_is_frozen_at_original_message_day():
    from app.semantic.compiler import compile_request
    from app.semantic.schemas import SemanticRequest

    pending = compiler()(
        [
            dict(
                domain="sales", operation="ranking", target="product", metric="unknown", time="昨天"
            )
        ],
        "昨天商品排行",
    )
    completed = compile_request(
        SemanticRequest(mode="answer", intents=[dict(metric="amount")]),
        question="按金额",
        today=date(2026, 9, 23),
        previous=pending.context,
    )
    assert str(completed.plan.steps[0].start_date) == "2026-09-21"


def test_order_count_summary_reuses_existing_summary_executor():
    result = compiler()(
        [dict(domain="sales", operation="summary", metric="orders", time="9月15号")],
        "9月15号订单数多少",
    )
    assert result.plan is not None and result.plan.steps[0].kind == "summary"
    assert result.context.intents[0].metric == "orders"


def test_structured_named_filter_is_preserved_when_date_is_completed():
    run = compiler()
    pending = run(
        [
            dict(
                domain="sales",
                operation="summary",
                filters=[dict(field="product", operator="include", value="阿莫西林")],
            )
        ],
        "阿莫西林卖了多少钱",
    )
    result = run([dict(time="9月15号")], "9月15号", previous=pending.context, mode="answer")
    assert result.plan is not None and result.context.intents[0].filters[0].value == "阿莫西林"
    assert result.plan.steps[0].filters[0].code == "阿莫西林"


def test_narrowing_combined_followup_keeps_filters():
    run = compiler()
    common = dict(
        domain="sales",
        time="9月15号",
        filters=[dict(field="category", operator="include", value="药品")],
    )
    pending = run(
        [dict(**common, operation="trend"), dict(**common, operation="ranking", target="product")],
        "9月15号只看药品趋势和排行",
    )
    result = run(
        [dict(domain="sales", operation="ranking", target="product")],
        "再看商品排行",
        previous=pending.context,
        mode="followup",
    )
    assert result.plan is None and result.context.intents[0].filters


def test_structured_amount_followup_replaces_previous_order_count():
    run = compiler()
    initial = run(
        [
            dict(
                domain="sales",
                operation="ranking",
                metric="orders",
                target="product",
                time="9月15号",
            )
        ],
        "9月15号商品订单数排行",
    )
    result = run([dict(metric="amount")], "改按金额排行", previous=initial.context, mode="followup")
    assert result.plan is not None and result.plan.steps[0].metric == "amount"


def test_missing_comparison_period_can_be_completed():
    run = compiler()
    pending = run(
        [dict(domain="sales", operation="comparison", time="9月15号")], "9月15号销售额比较"
    )
    result = run(
        [dict(comparison_time="9月14号")], "比较期9月14号", previous=pending.context, mode="answer"
    )
    assert result.plan is not None
    assert str(result.plan.steps[0].start_date) == "2026-09-15"
    assert str(result.plan.steps[0].comparison_start_date) == "2026-09-14"


def test_combined_ranking_metrics_stay_attached_to_their_structured_targets():
    result = compiler()(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="product",
                metric="amount",
                time="9月15号",
            ),
            dict(
                domain="sales", operation="ranking", target="buyer", metric="orders", time="9月15号"
            ),
        ],
        "9月15号商品按金额排行，客户按订单数排行",
    )
    assert [(step.kind, step.metric) for step in result.plan.steps] == [
        ("product_ranking", "amount"),
        ("buyer_ranking", "orders"),
    ]
