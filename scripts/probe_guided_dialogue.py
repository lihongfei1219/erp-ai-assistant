"""Opt-in cloud conversation acceptance using only a tiny synthetic report."""

import argparse
import asyncio
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.ai.model_client import load_model_settings
from app.analysis.dialogue import converse
from app.analysis.sales import analyze_sales
from app.connectors.qy import SalesExtract
from app.core.business_rules import load_business_rules
from app.schemas.sales import AnalysisWindow, DataScope
from app.semantic.dialogue_schemas import ConversationRequest
from app.semantic.provider import SemanticPlanner


def synthetic_report():
    orders = pd.DataFrame(
        [
            [
                1,
                "DEMO-GUIDE",
                "SYNTHETIC-BUYER",
                "2026-09-01",
                "订单完成",
                Decimal("10.0000"),
            ],
        ],
        columns=[
            "order_id",
            "order_number",
            "buyer_code",
            "created_at",
            "status",
            "amount",
        ],
    )
    lines = pd.DataFrame(
        [
            [1, 1, "SYNTHETIC-PRODUCT", 1, Decimal("10.0000"), Decimal("10.0000")],
        ],
        columns=[
            "order_id",
            "line_id",
            "product_code",
            "quantity",
            "unit_price",
            "amount",
        ],
    )
    return analyze_sales(
        SalesExtract(orders, lines, 1, Decimal("10.0000"), 0.0),
        AnalysisWindow(start="2026-09-01", end="2026-09-16"),
        DataScope(all_buyers=True),
        source_as_of=datetime.fromisoformat("2026-09-16T14:00:00+08:00"),
        rules=load_business_rules(),
        synthetic=True,
    )


async def probe(flow="all"):
    planner = SemanticPlanner(load_model_settings())
    report = synthetic_report()
    today = date(2026, 9, 22)
    failed = 0

    async def check(
        name, question, expected, previous=None, predicate=lambda outcome: True
    ):
        nonlocal failed
        started = time.monotonic()
        try:
            outcome = await converse(
                ConversationRequest(question=question),
                report,
                planner,
                today,
                previous=previous,
            )
            passed = outcome.turn.status == expected and predicate(outcome)
            failed += not passed
            print(
                name,
                "PASS" if passed else "FAIL",
                outcome.turn.status,
                f"{time.monotonic() - started:.2f}s",
                "field="
                + (
                    outcome.turn.clarification.field
                    if outcome.turn.clarification
                    else "none"
                ),
                flush=True,
            )
            return outcome
        except Exception as exc:  # noqa: BLE001 -- report safe type only, continue the probe.
            failed += 1
            print(name, "FAIL", type(exc).__name__, flush=True)
            return None

    if flow == "dimensions":
        await check(
            "region_dimension",
            "九月十号哪些地区成交频繁",
            "capability_gap",
            predicate=lambda o: (
                len(o.context.intents) == 1
                and o.context.intents[0].target == "region"
                and not o.context.intents[0].filters
                and o.turn.clarification.field == "target"
            ),
        )
        return failed

    if flow == "append":
        first = await check("base_summary", "9月5号整体销售情况", "result")
        if first:
            added = await check(
                "append_ranking",
                "保留这个概览，再加一份同一天商品金额排行",
                "result",
                first.context,
                predicate=lambda o: (
                    [i.operation for i in o.context.intents] == ["summary", "ranking"]
                ),
            )
            if added:
                await check(
                    "edit_added_goal",
                    "只把商品排行改成按订单数排，概览不动",
                    "result",
                    added.context,
                    predicate=lambda o: (
                        [i.metric for i in o.context.intents] == ["amount", "orders"]
                    ),
                )
        return failed

    if flow == "all":
        await check("colloquial", "九月五号那天什么商品卖的多", "result")
        pending = await check("broad", "我想了解销售情况，帮我看看", "needs_input")
        if pending:
            await check(
                "short_reply", "先看9月5号整体卖了多少钱", "result", pending.context
            )
    filtered = await check(
        "explicit_filter", "9月5号只看合成药品甲的销售额", "capability_gap"
    )
    if filtered:
        await check(
            "remove_filter",
            "取消商品限制，其他条件不变",
            "result",
            filtered.context,
            predicate=lambda o: all(not i.filters for i in o.context.intents),
        )
    if flow == "all":
        await check("data_gap", "今天卖了多少钱", "data_gap")
        combined = await check(
            "combined", "9月5号商品按销售金额排，客户按订单数排", "result"
        )
        if combined:
            await check(
                "targeted_change",
                "客户那个改成金额，商品那个别动",
                "result",
                combined.context,
                predicate=lambda o: (
                    len(o.context.intents) == 2
                    and all(i.metric == "amount" for i in o.context.intents)
                ),
            )
    return failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Send synthetic questions to configured cloud model",
    )
    parser.add_argument(
        "--flow", choices=["all", "filter", "append", "dimensions"], default="all"
    )
    args = parser.parse_args()
    if not args.probe:
        parser.error("Pass --probe to explicitly enable cloud requests")
    sys.exit(asyncio.run(probe(args.flow)))
