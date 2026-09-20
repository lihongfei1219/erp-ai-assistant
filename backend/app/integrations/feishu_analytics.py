"""Durable bridge between Feishu's synchronous worker and shared analysis planning."""

import asyncio
import hashlib
import json
from datetime import date

from pydantic import ValidationError

from app.ai.analytics_language import compact_question
from app.ai.sales_intent import parse_local_intent
from app.analysis.analytics import execute_analysis
from app.analysis.planning import describe_interpretation, plan_question
from app.analysis.sales_query import QueryUnavailable
from app.integrations.feishu_analytics_cards import render_analysis
from app.schemas.analytics import AnalysisPlan, AnalysisQuestion


def question_payload(question: str, today: date) -> dict:
    try:
        body = AnalysisQuestion(question=question)
    except ValidationError:
        raise QueryUnavailable("请将分析问题控制在1—1000字。") from None
    try:
        intent = parse_local_intent(question, today)
    except QueryUnavailable:
        return {"kind": "analysis_question", "question": body.question, "today": today.isoformat()}
    # Existing finite commands remain usable without a model call.
    kind = {
        "sales_summary": "summary",
        "sales_trend": "trend",
        "product_ranking": "product_ranking",
    }[intent.tool]
    plan = AnalysisPlan(steps=[dict(kind=kind, **intent.to_query().model_dump())])
    return {
        "kind": "analysis",
        "plan": plan.model_dump(mode="json"),
        "interpretation": describe_interpretation(body, plan),
        "product_scope_note": False,
    }


def snapshot_key(report) -> str:
    # Bind follow-ups and recovered plans to this exact snapshot and authorization scope.
    value = {
        "metadata": report.metadata.model_dump(mode="json"),
        "policy": report.operating.policy_fingerprint if report.operating else None,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _save(store, job, payload):
    if not store.replace_payload(job, payload):
        raise QueryUnavailable("任务已被重新领取，请重新提问。")
    job["payload"] = payload


def answer_analysis(job, store, report_loader, planner):
    payload = job["payload"]
    if payload["kind"] == "analysis_planning":
        raise QueryUnavailable("上次自然语言解析被中断，为避免重复调用，请重新提问。")
    if payload["kind"] == "analysis_question":
        # This durable marker also removes question text before any model request.
        _save(store, job, {"kind": "analysis_planning"})
        report = report_loader()
        key = snapshot_key(report)
        previous = store.latest_analysis_context(job)
        if previous and previous.get("snapshot_key") != key:
            previous = None
        body = AnalysisQuestion(
            question=payload["question"], previous_plan=previous["plan"] if previous else None
        )
        if planner is None or not planner.enabled:
            raise QueryUnavailable("自然语言模型暂不可用；发送“帮助”查看可用的固定日期查询示例。")
        plan, interpretation = asyncio.run(
            plan_question(body, report, planner, date.fromisoformat(payload["today"]))
        )
        product_scope_note = "药品" in compact_question(body.question) or bool(
            previous and previous.get("product_scope_note")
        )
        if product_scope_note and not any("未按药品类别筛选" in line for line in interpretation):
            interpretation.append(
                "沿用当前商品范围，未按药品类别筛选，可能包含器械、保健品等其他商品。"
            )
        payload = {
            "kind": "analysis",
            "plan": plan.model_dump(mode="json"),
            "interpretation": interpretation,
            "snapshot_key": key,
            "product_scope_note": product_scope_note,
        }
        _save(store, job, payload)
    else:
        report = report_loader()
        key = snapshot_key(report)
        if payload.get("snapshot_key", key) != key:
            raise QueryUnavailable("数据快照已更新，请重新提问以使用新的数据范围。")
        payload = dict(payload, snapshot_key=key)
        _save(store, job, payload)
    result = execute_analysis(report, AnalysisPlan.model_validate(payload["plan"]))
    result = result.model_copy(update={"interpretation": payload["interpretation"]})
    card, text = render_analysis(result)
    # Plotly figures are for the web; Feishu gets tabular facts and evidence IDs.
    stored = result.model_dump(mode="json", exclude={"results": {"__all__": {"chart"}}})
    stored["reply_card"] = card
    return stored, text
