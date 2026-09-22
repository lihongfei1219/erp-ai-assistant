"""Semantic changes retain each goal and require explicit, addressed removals."""

from datetime import date

import pytest

from app.semantic.compiler import compile_request
from app.semantic.schemas import SemanticContext, SemanticRequest

TODAY = date(2026, 9, 22)


def run(payload, question="9月15号销售情况", previous=None, **kwargs):
    return compile_request(
        SemanticRequest.model_validate(payload),
        question=question,
        today=TODAY,
        previous=previous,
        **kwargs,
    )


def draft(*, filtered=False, issues=None):
    intent = dict(domain="sales", operation="ranking", target="product", time="9月15号")
    if filtered:
        intent["filters"] = [dict(field="category", value="药品")]
    payload = {"intents": [intent]}
    if issues:
        payload["issues"] = issues
    return run(payload)


def two_goals():
    return run(
        {
            "intents": [
                dict(domain="sales", operation="ranking", target="product", time="9月14号"),
                dict(domain="sales", operation="ranking", target="buyer", time="9月15号"),
            ]
        },
        "9月14号商品排行和9月15号客户排行",
    )


def test_ids_stay_stable_and_default_provenance_survives_followup():
    first = draft()
    goal = first.context.intents[0]
    assert goal.id
    assert first.context.field_sources[goal.id]["metric"] == "default"
    second = run(
        {"mode": "followup", "intents": [dict(id=goal.id, limit=3)]}, "看前三", first.context
    )
    assert second.plan.steps[0].top_n == 3
    assert second.context.intents[0].id == goal.id
    assert second.context.field_sources[goal.id]["time"] == "inherited"
    assert second.context.field_sources[goal.id]["limit"] == "explicit"
    assert second.context.field_sources[goal.id]["metric"] == "default"


def test_one_addressed_patch_preserves_other_goal_and_its_different_date():
    first = two_goals()
    product, buyer = first.context.intents
    result = run(
        {"mode": "followup", "intents": [dict(id=buyer.id, metric="orders")]},
        "客户改按订单数",
        first.context,
    )
    assert result.status == "ready"
    assert [(s.kind, str(s.start_date), s.metric) for s in result.plan.steps] == [
        ("product_ranking", "2026-09-14", "amount"),
        ("buyer_ranking", "2026-09-15", "orders"),
    ]
    assert [i.id for i in result.context.intents] == [product.id, buyer.id]


def test_reordered_id_patches_do_not_swap_goal_fields():
    first = two_goals()
    product, buyer = first.context.intents
    result = run(
        {
            "mode": "followup",
            "intents": [
                dict(id=buyer.id, metric="orders"),
                dict(id=product.id, limit=2),
            ],
        },
        "客户按订单数，商品只看两个",
        first.context,
    )
    assert [(s.kind, s.metric, s.top_n) for s in result.plan.steps] == [
        ("product_ranking", "amount", 2),
        ("buyer_ranking", "orders", 10),
    ]


def test_ambiguous_unaddressed_patch_cannot_update_multiple_goals_by_position():
    first = two_goals()
    result = run(
        {"mode": "followup", "intents": [dict(metric="orders"), dict()]},
        "改成订单数",
        first.context,
    )
    assert result.status == "clarify"
    assert result.context.intents == first.context.intents
    assert result.context.issues[0].field == "target"


def test_unknown_goal_id_preserves_entire_previous_draft():
    first = draft()
    result = run(
        {"mode": "followup", "intents": [dict(id="stale-goal", metric="orders")]},
        "按订单数",
        first.context,
    )
    assert result.status == "clarify"
    assert result.context.intents == first.context.intents


def test_ambiguous_goal_keeps_the_pending_change_for_the_next_model_turn():
    first = two_goals()
    pending = run(
        {"mode": "followup", "intents": [dict(metric="orders")]},
        "换成订单数",
        first.context,
    )
    assert pending.status == "clarify"
    assert pending.context.intents == first.context.intents
    assert pending.context.pending_request.intents[0].metric == "orders"
    assert pending.context.pending_request.intents[0].id is None
    assert "pending_request" not in pending.context.pending_request.model_dump()
    # The model can now bind the remembered change to the customer's selected goal.
    resolved = run(
        {
            "mode": "answer",
            "intents": [dict(id=first.context.intents[0].id, metric="orders")],
            "resolved_issue_ids": [pending.context.issues[0].id],
        },
        "商品那个",
        pending.context,
    )
    assert resolved.status == "ready"
    assert [step.metric for step in resolved.plan.steps] == ["orders", "amount"]
    assert resolved.context.pending_request is None


def test_pending_change_is_not_automatically_applied_and_new_question_clears_it():
    first = two_goals()
    pending = run(
        {"mode": "followup", "intents": [dict(metric="orders")]},
        "换成订单数",
        first.context,
    )
    untouched = run(
        {
            "mode": "answer",
            "intents": [dict(id=first.context.intents[0].id)],
            "resolved_issue_ids": [pending.context.issues[0].id],
        },
        "维持原样",
        pending.context,
    )
    assert [step.metric for step in untouched.plan.steps] == ["amount", "amount"]
    assert untouched.context.pending_request is None
    independent = run(
        {"mode": "new", "intents": [dict(domain="sales", operation="summary", time="9月15号")]},
        "9月15号销售概览",
        pending.context,
    )
    assert independent.status == "ready"
    assert independent.context.pending_request is None


def test_rejected_field_value_is_not_saved_as_a_pending_target_change():
    first = draft()
    rejected = run(
        {
            "mode": "followup",
            "edits": [
                dict(
                    intent_id=first.context.intents[0].id,
                    operation="set",
                    field="metric",
                    value="invalid",
                )
            ],
        },
        "修改指标",
        first.context,
    )
    assert rejected.status == "clarify"
    assert rejected.context.pending_request is None


def test_adding_an_independent_goal_preserves_old_goal_and_inherits_shared_date():
    first = run(
        {"intents": [dict(domain="sales", operation="summary", time="9月15号", scope="all_buyers")]}
    )
    appended = run(
        {
            "mode": "followup",
            "add_intents": [
                dict(
                    domain="sales",
                    operation="ranking",
                    target="product",
                )
            ],
        },
        "保留概览，再加商品排行",
        first.context,
    )
    assert appended.status == "ready"
    assert [step.kind for step in appended.plan.steps] == ["summary", "product_ranking"]
    original, added = appended.context.intents
    assert original.id == first.context.intents[0].id
    assert added.id != original.id
    assert added.scope == "all_buyers"
    assert str(appended.plan.steps[1].start_date) == "2026-09-15"
    assert appended.context.field_sources[added.id]["time"] == "inherited"
    assert appended.context.field_sources[added.id]["scope"] == "inherited"
    assert appended.context.field_sources[added.id]["operation"] == "explicit"
    changed = run(
        {"mode": "followup", "intents": [dict(id=added.id, metric="orders")]},
        "新增商品排行改按订单数",
        appended.context,
    )
    assert [step.metric for step in changed.plan.steps] == ["amount", "orders"]
    assert [i.id for i in changed.context.intents] == [original.id, added.id]


def test_add_intents_requires_an_explicit_followup_and_existing_draft():
    first = draft()
    for mode, previous in (("answer", first.context), ("new", None), ("followup", None)):
        result = run(
            {"mode": mode, "add_intents": [dict(domain="sales", operation="summary")]},
            "加一个概览",
            previous,
        )
        assert result.status == "clarify"
        if previous:
            assert result.context.intents == first.context.intents


def test_added_goal_does_not_guess_between_different_dates():
    first = two_goals()
    appended = run(
        {"mode": "followup", "add_intents": [dict(domain="sales", operation="trend")]},
        "再加趋势",
        first.context,
    )
    assert appended.status == "clarify"
    assert len(appended.context.intents) == 3
    assert appended.context.intents[-1].time is None
    assert appended.context.issues[0].intent_id == appended.context.intents[-1].id


def test_added_goal_can_repeat_the_unique_verified_date_as_inherited():
    first = run({"intents": [dict(domain="sales", operation="summary", time="9月15号")]})
    appended = run(
        {
            "mode": "followup",
            "add_intents": [
                dict(
                    domain="sales",
                    operation="ranking",
                    target="product",
                    time="2026-09-15",
                )
            ],
        },
        "再加商品排行",
        first.context,
    )
    assert appended.status == "ready"
    added = appended.context.intents[-1]
    assert added.time == "2026-09-15至2026-09-15"
    assert appended.context.field_sources[added.id]["time"] == "inherited"


def test_added_goal_cannot_repeat_one_of_several_dates_without_customer_selection():
    first = two_goals()
    appended = run(
        {
            "mode": "followup",
            "add_intents": [
                dict(
                    domain="sales",
                    operation="trend",
                    time="2026-09-15",
                )
            ],
        },
        "再加趋势",
        first.context,
    )
    assert appended.status == "clarify"
    assert appended.context.intents[-1].time is None


def test_added_goal_keeps_common_filter_and_scope_instead_of_silently_widening():
    first = run(
        {
            "intents": [
                dict(
                    domain="sales",
                    operation="summary",
                    time="9月15号",
                    scope="all_buyers",
                    filters=[dict(field="category", value="药品")],
                )
            ]
        }
    )
    appended = run(
        {"mode": "followup", "add_intents": [dict(domain="sales", operation="ranking")]},
        "另加排行",
        first.context,
    )
    assert appended.status == "unsupported"
    assert [i.scope for i in appended.context.intents] == ["all_buyers", "all_buyers"]
    assert appended.context.intents[-1].filters[0].value == "药品"
    assert appended.context.intents[-1].filters[0].id != first.context.intents[0].filters[0].id


def test_added_goal_with_differing_filters_keeps_the_uncertainty_as_an_issue():
    first = run(
        {
            "intents": [
                dict(
                    domain="sales",
                    operation="summary",
                    time="9月15号",
                    filters=[dict(field="category", value="药品")],
                ),
                dict(
                    domain="sales",
                    operation="trend",
                    time="9月15号",
                    filters=[dict(field="category", value="器械")],
                ),
            ]
        }
    )
    appended = run(
        {"mode": "followup", "add_intents": [dict(domain="sales", operation="ranking")]},
        "另加排行",
        first.context,
    )
    assert appended.plan is None
    assert len(appended.context.intents) == 3
    assert any(
        i.field == "filters" and i.intent_id == appended.context.intents[-1].id
        for i in appended.context.issues
    )
    assert [i.filters[0].value for i in appended.context.intents[:2]] == ["药品", "器械"]


def test_added_goal_with_differing_scopes_needs_an_explicit_scope_choice():
    first = run(
        {
            "intents": [
                dict(domain="sales", operation="summary", time="9月15号", scope="all_buyers"),
                dict(domain="sales", operation="trend", time="9月15号", scope="authorized"),
            ]
        }
    )
    appended = run(
        {"mode": "followup", "add_intents": [dict(domain="sales", operation="ranking")]},
        "另加排行",
        first.context,
    )
    assert appended.status == "clarify"
    assert any(
        i.field == "scope" and i.intent_id == appended.context.intents[-1].id
        for i in appended.context.issues
    )


def test_more_than_six_goals_rejects_the_whole_append_and_keeps_previous_draft():
    first = run(
        {"intents": [dict(domain="sales", operation="summary", time="9月15号") for _ in range(6)]}
    )
    appended = run(
        {
            "mode": "followup",
            "intents": [dict(id=first.context.intents[0].id, metric="orders")],
            "add_intents": [dict(domain="sales", operation="ranking")],
        },
        "第一个改订单数，再加排行",
        first.context,
    )
    assert appended.status == "clarify"
    assert appended.context.intents == first.context.intents


def test_explicit_limit_on_summary_followup_is_not_silently_discarded():
    first = run({"intents": [dict(domain="sales", operation="summary", time="9月15号")]})
    result = run({"mode": "followup", "intents": [dict(limit=3)]}, "前三个", first.context)
    assert result.status == "clarify"
    assert result.context.intents[0].limit == 3
    assert result.context.issues[0].field == "operation"


def test_pending_summary_limit_survives_operation_answer_until_unset_or_ranking():
    first = run({"intents": [dict(domain="sales", operation="summary", time="9月15号")]})
    pending = run({"mode": "followup", "intents": [dict(limit=3)]}, "前三个", first.context)
    repeated = run(
        {
            "mode": "answer",
            "intents": [dict(operation="summary")],
            "resolved_issue_ids": [pending.context.issues[0].id],
        },
        "还是看概览",
        pending.context,
    )
    assert repeated.status == "clarify"
    assert repeated.context.intents[0].limit == 3
    assert repeated.context.pending == ["operation"]
    cancelled = run(
        {
            "mode": "answer",
            "edits": [
                dict(
                    intent_id=repeated.context.intents[0].id,
                    operation="unset",
                    field="limit",
                )
            ],
        },
        "不要条数限制",
        repeated.context,
    )
    assert cancelled.status == "ready"
    assert cancelled.context.intents[0].limit is None
    selected = run(
        {
            "mode": "answer",
            "intents": [dict(operation="ranking", target="product")],
            "resolved_issue_ids": [repeated.context.issues[0].id],
        },
        "看商品排行",
        repeated.context,
    )
    assert selected.status == "ready"
    assert selected.plan.steps[0].top_n == 3


def test_pending_date_is_verified_then_reused_when_customer_identifies_target():
    first = two_goals()
    pending = run(
        {"mode": "followup", "intents": [dict(time="9月2号")]}, "改到9月2号", first.context
    )
    assert pending.context.pending_request.intents[0].time == "2026-09-02至2026-09-02"
    completed = run(
        {
            "mode": "answer",
            "intents": [dict(id=first.context.intents[0].id, time="2026-09-02至2026-09-02")],
            "resolved_issue_ids": [pending.context.issues[0].id],
        },
        "商品那个",
        pending.context,
    )
    assert completed.status == "ready"
    assert [str(step.start_date) for step in completed.plan.steps] == ["2026-09-02", "2026-09-15"]


def test_pending_patch_drops_a_model_invented_date_and_cannot_later_authorize_it():
    first = two_goals()
    pending = run(
        {"mode": "followup", "intents": [dict(metric="orders", time="9月2号")]},
        "改成订单数",
        first.context,
    )
    assert pending.context.pending_request.intents[0].time is None
    completed = run(
        {
            "mode": "answer",
            "intents": [
                dict(id=first.context.intents[0].id, time="2026-09-02至2026-09-02", metric="orders")
            ],
            "resolved_issue_ids": [pending.context.issues[0].id],
        },
        "商品那个",
        pending.context,
    )
    assert completed.status == "clarify"
    assert completed.context.intents[0].time is None


def test_explicit_filter_replacement_and_removal_keep_other_fields():
    first = draft(filtered=True)
    goal = first.context.intents[0]
    constraint = goal.filters[0]
    assert constraint.id
    replaced = run(
        {
            "mode": "followup",
            "edits": [
                dict(
                    intent_id=goal.id,
                    operation="replace_filter",
                    constraint_id=constraint.id,
                    filter=dict(field="category", value="器械"),
                )
            ],
        },
        "把药品换成器械",
        first.context,
    )
    changed = replaced.context.intents[0]
    assert changed.id == goal.id and changed.time == goal.time
    assert [(f.id, f.value) for f in changed.filters] == [(constraint.id, "器械")]
    removed = run(
        {
            "mode": "followup",
            "edits": [
                dict(
                    intent_id=goal.id,
                    operation="remove_filter",
                    constraint_id=constraint.id,
                )
            ],
        },
        "取消这个分类限制",
        replaced.context,
    )
    assert removed.status == "ready"
    assert removed.context.intents[0].filters == []


def test_unknown_constraint_id_does_not_remove_any_filter():
    first = draft(filtered=True)
    result = run(
        {
            "mode": "followup",
            "edits": [
                dict(
                    intent_id=first.context.intents[0].id,
                    operation="remove_filter",
                    constraint_id="bad",
                )
            ],
        },
        "取消条件",
        first.context,
    )
    assert result.status == "clarify"
    assert result.context.intents[0].filters == first.context.intents[0].filters


def test_explicit_goal_removal_leaves_the_other_goal_ready():
    first = two_goals()
    result = run(
        {
            "mode": "followup",
            "edits": [
                dict(
                    intent_id=first.context.intents[0].id,
                    operation="remove_intent",
                )
            ],
        },
        "只保留客户排行",
        first.context,
    )
    assert result.status == "ready"
    assert [s.kind for s in result.plan.steps] == ["buyer_ranking"]


def test_issue_resolution_uses_id_and_leaves_unanswered_issues():
    first = draft(
        issues=[
            dict(
                field="metric",
                kind="ambiguous",
                question="希望按什么比较？",
                choices=[dict(label="金额", value="amount"), dict(label="订单数", value="orders")],
            ),
            dict(field="conditions", kind="ambiguous", question="还有限制条件吗？"),
        ]
    )
    goal = first.context.intents[0]
    metric_issue, other_issue = first.context.issues
    result = run(
        {
            "mode": "answer",
            "intents": [dict(id=goal.id, metric="amount")],
            "resolved_issue_ids": [metric_issue.id],
        },
        "按金额",
        first.context,
    )
    assert result.status == "clarify"
    assert [i.id for i in result.context.issues] == [other_issue.id]


def test_dependent_goals_cannot_be_partially_removed():
    first = run(
        {
            "intents": [
                dict(domain="sales", operation="ranking", time="9月15号"),
                dict(domain="inventory", operation="ranking"),
            ],
            "issues": [
                dict(field="dependency", kind="dependency", question="销售排行需要库存条件筛选。")
            ],
        }
    )
    result = run(
        {
            "mode": "followup",
            "edits": [
                dict(
                    intent_id=first.context.intents[1].id,
                    operation="remove_intent",
                )
            ],
        },
        "只查销售",
        first.context,
    )
    assert result.plan is None
    assert any(i.kind == "dependency" for i in result.context.issues)


def test_server_trusted_dates_compile_absolute_draft_without_customer_text():
    request = {"intents": [dict(domain="sales", operation="summary", time="2026-09-15")]}
    assert run(request, "", trusted_dates=True).status == "ready"
    assert run(request, "").status == "clarify"


def test_comparison_date_cannot_be_invented_when_user_only_supplied_main_period():
    result = run(
        {
            "intents": [
                dict(
                    domain="sales",
                    operation="comparison",
                    time="9月15号",
                    comparison_time="9月14号",
                )
            ]
        }
    )
    assert result.plan is None


def test_broad_sales_goal_asks_about_analysis_purpose_before_dates():
    result = run({"intents": [dict(domain="sales")]}, "看看经营情况")
    assert result.status == "clarify"
    assert result.context.issues[0].field == "operation"


def test_sales_and_stock_request_explains_known_inventory_gap_before_sales_details():
    result = run({"intents": [dict(domain="sales"), dict(domain="inventory")]}, "看看销售和库存")
    assert result.status == "unsupported"
    assert "库存" in result.message
    assert result.context.intents[0].operation is None


def test_unsupported_metric_cannot_preserve_an_invented_date_as_trusted_state():
    result = run(
        {"intents": [dict(domain="sales", operation="ranking", metric="quantity", time="9月15号")]},
        "看看销量排行",
    )
    assert result.context.intents[0].time is None


def test_old_text_constraint_is_resolved_by_its_program_assigned_issue_id():
    first = run(
        {
            "intents": [dict(domain="sales", operation="ranking", time="9月15号")],
            "unresolved": ["金额还是订单数"],
        }
    )
    result = run(
        {
            "mode": "answer",
            "intents": [dict(metric="orders")],
            "resolved_issue_ids": [first.context.issues[0].id],
        },
        "看订单数",
        first.context,
    )
    assert result.status == "ready"
    assert result.context.unresolved == []


def test_resolving_same_worded_issue_for_one_goal_does_not_clear_another():
    first = run(
        {
            "intents": [
                dict(id="p", domain="sales", operation="ranking", target="product", time="9月15号"),
                dict(id="b", domain="sales", operation="ranking", target="buyer", time="9月15号"),
            ],
            "issues": [
                dict(intent_id="p", field="metric", question="按什么指标？"),
                dict(intent_id="b", field="metric", question="按什么指标？"),
            ],
        }
    )
    result = run(
        {
            "mode": "answer",
            "intents": [dict(id=first.context.intents[0].id, metric="amount")],
            "resolved_issue_ids": [first.context.issues[0].id],
        },
        "商品看金额",
        first.context,
    )
    assert result.status == "clarify"
    assert [i.id for i in result.context.issues] == [first.context.issues[1].id]


def test_rephrasing_same_field_question_keeps_one_stable_issue():
    first = draft(issues=[dict(field="metric", question="用什么衡量表现？")])
    result = run(
        {
            "mode": "answer",
            "intents": [dict(id=first.context.intents[0].id)],
            "issues": [
                dict(
                    intent_id=first.context.intents[0].id,
                    field="metric",
                    question="你比较在意金额还是订单数？",
                )
            ],
        },
        "还是不明白",
        first.context,
    )
    assert result.status == "clarify"
    assert len(result.context.issues) == 1
    assert result.context.issues[0].id == first.context.issues[0].id
    assert "订单数" in result.context.issues[0].question


def test_invalid_edit_without_prior_draft_returns_an_identified_clarification_draft():
    result = run(
        {
            "mode": "answer",
            "edits": [
                dict(
                    intent_id="missing-goal",
                    operation="set",
                    field="metric",
                    value="amount",
                )
            ],
        },
        "改成金额",
    )
    assert result.status == "clarify"
    assert result.context.intents[0].id


def test_legacy_context_without_ids_accepts_original_empty_positional_patches():
    previous = SemanticContext(
        intents=[
            dict(domain="sales", operation="ranking", target="product", time="2026-09-15"),
            dict(domain="sales", operation="ranking", target="buyer", time="2026-09-15"),
        ]
    )
    result = run(
        {"mode": "followup", "intents": [dict(metric="orders"), dict()]}, "商品改订单数", previous
    )
    assert result.status == "ready"
    assert [s.metric for s in result.plan.steps] == ["orders", "amount"]


def test_legacy_restatement_context_is_displayable_with_stable_goal_and_filter_ids():
    previous = SemanticContext(
        intents=[
            dict(
                domain="sales",
                operation="summary",
                filters=[dict(field="category", value="药品")],
            )
        ],
        requires_restatement=True,
    )
    first = run({"mode": "answer", "intents": [dict()]}, "继续", previous)
    assert first.status == "clarify"
    assert first.context.intents[0].id
    assert first.context.intents[0].filters[0].id
    second = run({"mode": "answer", "intents": [dict()]}, "继续", first.context)
    assert second.context.intents[0].id == first.context.intents[0].id
    assert second.context.intents[0].filters[0].id == first.context.intents[0].filters[0].id


@pytest.mark.parametrize("field,value", [("metric", "bogus"), ("limit", 0), ("id", "injected")])
def test_invalid_edit_does_not_mutate_known_goal(field, value):
    first = draft()
    result = run(
        {
            "mode": "followup",
            "edits": [
                dict(
                    intent_id=first.context.intents[0].id,
                    operation="set",
                    field=field,
                    value=value,
                )
            ],
        },
        "修改",
        first.context,
    )
    assert result.status == "clarify"
    assert result.context.intents == first.context.intents
