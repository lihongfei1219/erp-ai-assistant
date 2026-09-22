"""Opt-in cloud smoke checks built only from synthetic records; never loads ERP snapshots."""

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.ai.model_client import load_model_settings
from app.analysis.dialogue import converse
from app.semantic.dialogue_schemas import ConversationRequest
from app.semantic.provider import SemanticPlanner


def synthetic_report():
    from probe_guided_dialogue import synthetic_report as sales_fixture

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "frontend/tests"))
    from operations_fixture import with_operations

    return with_operations(sales_fixture())


def plans_match(outcome, expected):
    """Check every goal, including its metric and period, without depending on goal order."""
    if outcome.turn.status != "result" or outcome.turn.result is None:
        return False
    actual = [
        (
            s.domain,
            s.kind,
            "stock" if s.domain == "inventory" and s.metric == "quantity" else s.metric,
            s.start_date.isoformat(),
            s.end_date_exclusive.isoformat(),
        )
        for s in outcome.turn.result.plan.steps
    ]
    outputs = [(r.domain, r.kind) for r in outcome.turn.result.results]
    return sorted(actual) == sorted(expected) and sorted(outputs) == sorted(
        (domain, kind) for domain, kind, *_ in expected
    )


def step(domain, kind="summary", metric="amount", start="2026-09-15", end="2026-09-16"):
    return domain, kind, metric, start, end


async def probe(flow="smoke"):
    report = synthetic_report()
    planner = SemanticPlanner(load_model_settings())
    failed = 0
    skipped = 0
    checked = 0

    async def check(name, body, predicate, previous=None):
        nonlocal failed, checked
        checked += 1
        started = monotonic()
        try:
            outcome = await converse(body, report, planner, date(2026, 9, 22), previous=previous)
            passed = predicate(outcome)
            info = dict(
                case=name,
                passed=passed,
                status=outcome.turn.status,
                clarification=outcome.turn.clarification.field
                if outcome.turn.clarification
                else None,
                fields=[
                    dict(
                        domain=i.domain,
                        operation=i.operation,
                        metric=i.metric,
                        target=i.target,
                        time=i.time,
                    )
                    for i in outcome.context.intents
                ],
            )
        except Exception as exc:  # noqa: BLE001 -- log safe type only; never provider payloads.
            passed = False
            outcome = None
            info = dict(case=name, passed=False, error_type=type(exc).__name__)
        info["seconds"] = round(monotonic() - started, 2)
        print(json.dumps(info, ensure_ascii=True), flush=True)
        failed += not passed
        return outcome if passed else None

    def skip(name):
        nonlocal skipped
        skipped += 1
        print(json.dumps(dict(case=name, skipped="prerequisite_failed")), flush=True)

    if flow in {"smoke", "all"}:
        cases = [
            (
                "returns",
                "九月一号到十五号退了多少钱",
                [step("returns", start="2026-09-01")],
            ),
            (
                "shipping",
                "九月十五号出库了哪些商品",
                [step("shipping", "list", "orders")],
            ),
            (
                "inventory",
                "九月十六号哪些商品库存多",
                [
                    step(
                        "inventory",
                        "product_ranking",
                        "stock",
                        "2026-09-16",
                        "2026-09-17",
                    )
                ],
            ),
        ]
        for domain, question, expected in cases:
            await check(
                domain,
                ConversationRequest(question=question),
                lambda o, e=expected: plans_match(o, e),
            )

    if flow in {"dialogue", "all"}:
        base = [step(d) for d in ("sales", "returns", "shipping")]
        combo = await check(
            "three_domain_combo",
            ConversationRequest(question="分别看9月15号的销售金额、退货金额和销售出库金额"),
            lambda o: plans_match(o, base),
        )
        edited = [base[0], base[1], step("shipping", "product_ranking", "quantity")]
        if combo:
            change = await check(
                "edit_shipping_only",
                ConversationRequest(question="只把出库那项改成按商品数量排行，销售和退货保持不变"),
                lambda o: plans_match(o, edited),
                combo.context,
            )
        else:
            change = None
            skip("edit_shipping_only")
        if change:
            await check(
                "append_inventory_with_own_date",
                ConversationRequest(question="保留这三项，再加一份9月16号的商品库存数量排行"),
                lambda o: plans_match(
                    o,
                    edited
                    + [
                        step(
                            "inventory",
                            "product_ranking",
                            "stock",
                            "2026-09-16",
                            "2026-09-17",
                        )
                    ],
                ),
                change.context,
            )
        else:
            skip("append_inventory_with_own_date")

    if flow in {"inventory", "dialogue", "all"}:
        stock = await check(
            "today_inventory_data_gap",
            ConversationRequest(question="现在库存最多的是哪些商品"),
            lambda o: (
                o.turn.status == "data_gap"
                and o.turn.result is None
                and len(o.context.intents) == 1
                and o.context.intents[0].domain == "inventory"
                and o.turn.available_dates.start == date(2026, 9, 16)
            ),
        )
        snapshot_choice = (
            next((c for c in stock.turn.choices if "2026-09-16" in c.label), None)
            if stock
            else None
        )
        if snapshot_choice:
            selected = await check(
                "explicit_inventory_snapshot_choice",
                ConversationRequest(choice_id=snapshot_choice.id),
                lambda o: (
                    plans_match(
                        o,
                        [
                            step(
                                "inventory",
                                "product_ranking",
                                "stock",
                                "2026-09-16",
                                "2026-09-17",
                            )
                        ],
                    )
                    and o.turn.result.results[0].totals["as_of"]
                    == report.metadata.source_as_of.isoformat()
                ),
                stock.context,
            )
            if selected:
                await check(
                    "inventory_followup_returns_to_current_time",
                    ConversationRequest(question="那现在的库存呢"),
                    lambda o: (
                        o.turn.status == "data_gap"
                        and o.turn.result is None
                        and len(o.context.intents) == 1
                        and o.context.intents[0].domain == "inventory"
                        and o.context.intents[0].time == "2026-09-22至2026-09-22"
                    ),
                    selected.context,
                )
            else:
                skip("inventory_followup_returns_to_current_time")
        else:
            skip("explicit_inventory_snapshot_choice")
            skip("inventory_followup_returns_to_current_time")

    if flow in {"dialogue", "all"}:
        pending = await check(
            "returns_missing_date",
            ConversationRequest(question="帮我看看退货金额"),
            lambda o: (
                o.turn.status == "needs_input"
                and o.turn.clarification.field == "time"
                and o.context.intents[0].domain == "returns"
            ),
        )
        if pending:
            dated = await check(
                "returns_free_text_date_reply",
                ConversationRequest(question="九月十五号"),
                lambda o: plans_match(o, [step("returns")]),
                pending.context,
            )
        else:
            dated = None
            skip("returns_free_text_date_reply")
        if dated:
            await check(
                "returns_customer_followup",
                ConversationRequest(question="那天哪些客户退货金额最多"),
                lambda o: plans_match(o, [step("returns", "buyer_ranking")]),
                dated.context,
            )
        else:
            skip("returns_customer_followup")

        await check(
            "unsupported_rate_preserves_combo",
            ConversationRequest(question="分别看9月15号的商品退货率排行和销售总金额"),
            lambda o: (
                o.turn.status == "capability_gap"
                and o.turn.result is None
                and len(o.context.intents) == 2
                and {(i.domain, i.metric) for i in o.context.intents}
                == {("returns", "return_rate"), ("sales", "amount")}
            ),
        )
    if flow in {"recovery", "all"}:
        for domain, target, question in [
            ("returns", "buyer", "保留9月15号销售金额概览，另比较9月15号和9月14号各客户的退货单数"),
            (
                "shipping",
                "product",
                "分别看9月15号销售总金额，以及9月15号相比9月14号各商品的出库单数",
            ),
        ]:

            def correct_gap(outcome, domain=domain, target=target):
                goals = {i.domain: i for i in outcome.context.intents}
                return (
                    outcome.turn.status == "capability_gap"
                    and outcome.turn.result is None
                    and len(goals) == len(outcome.context.intents) == 2
                    and set(goals) == {"sales", domain}
                    and goals[domain].metric == "orders"
                    and goals[domain].target == target
                    and goals[domain].time == "2026-09-15至2026-09-15"
                    and goals[domain].comparison_time == "2026-09-14至2026-09-14"
                    and outcome.turn.clarification.field == "operation"
                    and outcome.turn.clarification.intent_id == goals[domain].id
                    and len(outcome.turn.choices) == 3
                )

            gap = await check(
                domain + "_comparison_gap", ConversationRequest(question=question), correct_gap
            )
            if gap:
                for choice, kind in zip(
                    gap.turn.choices, ("summary", target + "_ranking", "list"), strict=True
                ):
                    await check(
                        domain + "_recover_" + kind,
                        ConversationRequest(choice_id=choice.id),
                        lambda o, d=domain, k=kind: plans_match(
                            o, [step("sales"), step(d, k, "orders")]
                        ),
                        gap.context,
                    )
            else:
                for kind in ("summary", "ranking", "list"):
                    skip(domain + "_recover_" + kind)

    print(json.dumps(dict(checked=checked, failed=failed, skipped=skipped)), flush=True)
    return int(bool(failed or skipped))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", required=True)
    parser.add_argument(
        "--flow", choices=["smoke", "inventory", "dialogue", "recovery", "all"], default="smoke"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(probe(args.flow)))
