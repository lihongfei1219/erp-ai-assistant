"""Synthetic-only semantic cloud evaluation; no ERP reads and no Feishu messages."""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.ai.model_client import load_model_settings
from app.semantic.compiler import compile_request
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticContext, SemanticInput


async def probe(selected=None):
    planner = SemanticPlanner(load_model_settings())
    cases = [
        ("single_day", "九月十五号哪个药品卖得最好", "ready", ["sales"]),
        ("colloquial_more", "九月五号那天什么商品卖的多", "ready", ["sales"]),
        ("colloquial_more_variant", "九月五号卖得多的商品有哪些", "ready", ["sales"]),
        ("explicit_units", "九月五号哪些商品卖的件数多", "unsupported", ["sales"]),
        (
            "colloquial_orders",
            "九月五号什么商品卖的多，按订单数排",
            "ready",
            ["sales"],
        ),
        (
            "colloquial_combined",
            "九月五号什么商品卖的多，同时看客户订单数排行",
            "ready",
            ["sales", "sales"],
        ),
        (
            "colloquial_combined_units",
            "九月五号什么商品卖的多，同时看客户采购件数排行",
            "unsupported",
            ["sales", "sales"],
        ),
        ("today", "今天卖了多少钱", "ready", ["sales"]),
        ("returns", "九月十五号有退货吗", "unsupported", ["returns"]),
        ("shipping", "九月十五号出库了多少商品", "unsupported", ["shipping"]),
        ("inventory", "库存多的是哪些药品", "unsupported", ["inventory"]),
        (
            "combined",
            "9月1日至5号每日销售趋势和商品销售额前5名",
            "ready",
            ["sales", "sales"],
        ),
        (
            "mixed",
            "9月15号卖了多少金额，退货金额多少",
            "unsupported",
            ["sales", "returns"],
        ),
        ("quantity", "9月15号药品销量前五名", "unsupported", ["sales"]),
        ("filter", "9月15号只看药品，排除器械的销售额", "unsupported", ["sales"]),
        ("pending", "这几天卖了多少钱", "clarify", ["sales"]),
    ]
    failures = 0
    previous = None
    for name, question, status, domains in cases:
        if selected and name not in selected:
            continue
        request = SemanticInput(question=question, today=date(2026, 9, 22))
        try:
            semantic = await planner.interpret(request)
            result = compile_request(semantic, question=question, today=request.today)
            correct = (
                result.status == status
                and [i.domain for i in result.context.intents] == domains
            )
            if name == "single_day" and result.plan:
                step = result.plan.steps[0]
                correct = (
                    correct and str(step.start_date) == "2026-09-15" and step.top_n == 1
                )
            if name == "today" and result.plan:
                correct = (
                    correct and str(result.plan.steps[0].start_date) == "2026-09-22"
                )
            if name.startswith("colloquial_more"):
                correct = correct and result.plan is not None
                if result.plan:
                    step = result.plan.steps[0]
                    correct = correct and (
                        step.kind == "product_ranking"
                        and step.metric == "amount"
                        and step.top_n == 10
                        and str(step.start_date) == "2026-09-05"
                        and str(step.end_date_exclusive) == "2026-09-06"
                    )
            if name in {"colloquial_orders", "colloquial_combined"}:
                expected = (
                    [("product_ranking", "orders")]
                    if name == "colloquial_orders"
                    else [("product_ranking", "amount"), ("buyer_ranking", "orders")]
                )
                correct = correct and result.plan is not None
                if result.plan:
                    correct = (
                        correct
                        and [(s.kind, s.metric) for s in result.plan.steps] == expected
                    )
            if name == "explicit_units":
                correct = correct and [
                    (i.target, i.metric) for i in result.context.intents
                ] == [("product", "quantity")]
            if name == "colloquial_combined_units":
                by_target = {i.target: i.metric for i in result.context.intents}
                correct = (
                    correct
                    and by_target.get("product") in {None, "amount"}
                    and by_target.get("buyer") == "quantity"
                )
            if name == "combined" and result.plan:
                correct = correct and [s.kind for s in result.plan.steps] == [
                    "trend",
                    "product_ranking",
                ]
                correct = correct and result.plan.steps[1].top_n == 5
            if name == "pending":
                previous = result.context
            print(name, "PASS" if correct else "FAIL", result.status, flush=True)
            if not correct:
                # Enum-only diagnostics: never log question text or raw provider content.
                print(
                    "diagnostic",
                    {
                        "pending": result.context.pending,
                        "goals": [
                            (i.domain, i.operation, i.metric)
                            for i in result.context.intents
                        ],
                        "issues": [(i.field, i.kind) for i in result.context.issues],
                    },
                    flush=True,
                )
            failures += not correct
        except Exception as exc:  # noqa: BLE001 - sanitize provider failures in this probe
            chain = []
            seen = set()
            while exc is not None and id(exc) not in seen:
                seen.add(id(exc))
                chain.append(type(exc).__name__)
                exc = exc.__cause__ or exc.__context__
            print(name, "FAIL", "/".join(chain), flush=True)
            failures += 1
    if previous is not None:
        request = SemanticInput(
            question="三天", today=date(2026, 9, 22), previous=previous
        )
        try:
            semantic = await planner.interpret(request)
            result = compile_request(
                semantic,
                question=request.question,
                today=request.today,
                previous=previous,
            )
            correct = (
                result.plan is not None
                and str(result.plan.steps[0].start_date) == "2026-09-19"
            )
            print("complete_pending", "PASS" if correct else "FAIL", flush=True)
            failures += not correct
        except Exception as exc:  # noqa: BLE001 - sanitize provider failures in this probe
            print("complete_pending", "FAIL", type(exc).__name__, flush=True)
            failures += 1
    for metric in ("orders", "quantity"):
        name = "colloquial_followup_" + metric
        if selected and name not in selected:
            continue
        previous = SemanticContext(
            intents=[
                {
                    "domain": "sales",
                    "operation": "ranking",
                    "target": "product",
                    "metric": metric,
                    "time": "2026-09-04至2026-09-04",
                    "limit": 10,
                }
            ]
        )
        request = SemanticInput(
            question="那改看九月五号，哪些卖得多",
            today=date(2026, 9, 22),
            previous=previous,
        )
        try:
            semantic = await planner.interpret(request)
            result = compile_request(
                semantic,
                question=request.question,
                today=request.today,
                previous=previous,
            )
            expected_status = "ready" if metric == "orders" else "unsupported"
            correct = (
                result.status == expected_status
                and result.context.intents[0].metric == metric
                and result.context.intents[0].time == "2026-09-05至2026-09-05"
            )
            print(name, "PASS" if correct else "FAIL", flush=True)
            failures += not correct
        except Exception as exc:  # noqa: BLE001 - sanitize provider failures in this probe
            print(name, "FAIL", type(exc).__name__, flush=True)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe", action="store_true", help="Explicitly call configured cloud model"
    )
    parser.add_argument(
        "--case", action="append", help="Run only named cases (repeatable)"
    )
    args = parser.parse_args()
    if not args.probe:
        parser.error("Use --probe to opt into synthetic cloud requests")
    raise SystemExit(asyncio.run(probe(args.case)))
