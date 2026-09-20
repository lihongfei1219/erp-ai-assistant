"""Explicit synthetic-only model evaluation. Never loads ERP reports or sends messages."""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.ai.analytics_agent import AnalyticsPlanner
from app.ai.analytics_bounds import validate_question_plan
from app.ai.model_client import load_model_settings
from app.schemas.analytics import AnalysisQuestion

CONTEXT = {
    "today": "2026-09-20",
    "available_start": "2026-09-01",
    "available_end_exclusive": "2026-09-16",
    "timezone": "Asia/Shanghai",
}


async def probe(settings):
    planner = AnalyticsPlanner(settings)
    cases = [
        ("summary", "统计2026年9月1日至7日销售概览", {"summary"}),
        (
            "combined",
            "2026年9月1日至7日每日销售趋势，并列出商品销售额前5名",
            {"trend", "product_ranking"},
        ),
        (
            "comparison",
            "比较2026年9月8日至14日与2026年9月1日至7日的销售额，按商品拆解变化贡献",
            {"comparison"},
        ),
        ("unsupported", "2026年9月1日至7日利润是多少", None),
        ("uncovered", "昨天销售额是多少", None),
        (
            "colloquial_drugs",
            "分析2026年9月1日至5号的销售数据，哪些药品卖的好",
            {"product_ranking"},
        ),
        (
            "colloquial_products",
            "麻烦帮我看看2026年9月1日至5日哪些产品比较畅销，谢谢",
            {"product_ranking"},
        ),
        (
            "numeric_dates",
            "2026/9/1到2026/9/5，哪些商品卖得不错，给我列个榜单",
            {"product_ranking"},
        ),
        (
            "explicit_orders",
            "2026年9月1日至7日药品按订单数排前5名",
            {"product_ranking"},
        ),
        ("explicit_category", "2026年9月1日至7日只看药品，排除器械的销售排行", None),
        ("explicit_quantity", "2026年9月1日至7日药品销量最高的前5名", None),
    ]
    for name, question, kinds in cases:
        decision = await planner.plan(AnalysisQuestion(question=question), CONTEXT)
        if decision.action == "run":
            validate_question_plan(
                AnalysisQuestion(question=question), decision.plan, date(2026, 9, 20)
            )
        correct = (
            decision.action == "clarify"
            if kinds is None
            else (
                decision.action == "run"
                and {step.kind for step in decision.plan.steps} == kinds
            )
        )
        if correct and kinds is not None:
            for step in decision.plan.steps:
                dates = (
                    step.start_date.isoformat(),
                    step.end_date_exclusive.isoformat(),
                )
                correct = correct and dates == (
                    ("2026-09-08", "2026-09-15")
                    if name == "comparison"
                    else ("2026-09-01", "2026-09-06")
                    if name
                    in {"colloquial_drugs", "colloquial_products", "numeric_dates"}
                    else ("2026-09-01", "2026-09-08")
                )
                if step.kind == "product_ranking":
                    correct = correct and step.top_n == (
                        5 if name in {"combined", "explicit_orders"} else 10
                    )
                    correct = correct and step.metric == (
                        "orders" if name == "explicit_orders" else "amount"
                    )
                if step.kind == "comparison":
                    correct = (
                        correct and str(step.comparison_start_date) == "2026-09-01"
                    )
                    correct = (
                        correct
                        and str(step.comparison_end_date_exclusive) == "2026-09-08"
                    )
        print(name, "PASS" if correct else "FAIL", flush=True)
        if not correct:
            return 1
    from app.schemas.analytics import AnalysisPlan

    previous = AnalysisPlan(
        steps=[
            {
                "kind": "product_ranking",
                "start_date": "2026-09-01",
                "end_date_exclusive": "2026-09-06",
            }
        ]
    )
    for question, metric, top in [
        ("换成按订单数排，取前5名", "orders", 5),
        ("取前3名", "orders", 3),
    ]:
        body = AnalysisQuestion(question=question, previous_plan=previous)
        decision = await planner.plan(body, CONTEXT)
        correct = decision.action == "run"
        if correct:
            validate_question_plan(body, decision.plan, date(2026, 9, 20))
            correct = (
                decision.plan.steps[0].metric == metric
                and decision.plan.steps[0].top_n == top
            )
            previous = decision.plan
        print("followup", "PASS" if correct else "FAIL", flush=True)
        if not correct:
            return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    try:
        settings = load_model_settings()
        print("Model configured:", settings.enabled, flush=True)
        if not args.probe:
            return 0 if settings.enabled else 1
        return asyncio.run(probe(settings))
    except Exception as exc:  # noqa: BLE001 - never print provider secrets in a probe traceback
        print("Probe failed:", type(exc).__name__, "(details and credentials omitted)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
