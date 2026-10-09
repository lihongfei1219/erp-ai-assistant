"""Present structured dialogue decisions; never interpret or rewrite customer intent."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from app.analysis.analytics import available_dates
from app.analysis.operations import DOMAIN_LABELS, domain_dates, executable_domains
from app.integrations.feishu_conversation import (
    MAX_CONVERSATION_ROUNDS,
    conversation_closed,
    round_count,
    round_notice,
)
from app.integrations.feishu_display_components import panel, text
from app.semantic.dialogue_schemas import DialogueTurn


def numbered_choices(turn: DialogueTurn):
    # Keep exactly the same sequence for display and bound numeric replies.
    return [choice for choice in turn.choices if choice.action.kind != "date_range"]


def render_guidance(payload):
    turn = (
        DialogueTurn.model_validate(payload["dialogue_turn"])
        if payload.get("dialogue_turn")
        else None
    )
    if conversation_closed(payload):
        sections = [round_notice(payload)]
        sections.append(payload.get("notice") or (
            "当前条件尚未形成可执行的分析。请把日期、分析指标及品种或客户范围"
            "合在一条完整消息中重新提问。"
        ))
        details = []
        if turn:
            details.append("本次已理解的需求（供重新提问时参考）\n" + turn.understood_summary)
            if turn.clarification:
                details.append("尚未解决的事项\n" + turn.clarification.question)
        elements = [text(section) for section in sections]
        if details:
            elements.append(panel("查看本次需求与未解决事项", "\n\n".join(details)))
        title = "本次对话已结束"
        card = {
            "schema": "2.0",
            "config": {"width_mode": "fill", "summary": {"content": title}},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
            "body": {"padding": "12px", "vertical_spacing": "8px", "elements": elements},
        }
        return {"semantic_status": payload["status"], "reply_card": card}, "\n\n".join(sections)
    title = "确认一下你的想法"
    sections, details = [], []
    if payload.get("notice"):
        title = "请用文字说明你的选择"
        sections.append(payload["notice"])
    if turn is not None:
        if turn.status == "data_gap" and not payload.get("notice"):
            title = "所选日期暂无完整数据"
        if turn.understood_summary:
            details.append("已理解的需求\n" + turn.understood_summary)
        if turn.applied_defaults:
            details.append("默认条件（可以修改）\n" + "；".join(turn.applied_defaults))
        if turn.clarification is not None:
            question = turn.clarification.question
            selected = next(
                (item for item in turn.draft.intents if item.id == turn.clarification.intent_id),
                None,
            )
            growth = bool(
                selected
                and selected.fields.get("domain") == "shipping"
                and selected.fields.get("operation") in {"growth", "price", "margin"}
            )
            details.append("待确认事项\n" + question)
            if len(question) > 100 and numbered_choices(turn) and not growth:
                question = {
                    "capability_gap": "当前条件暂不支持，要调整为下面哪一种？",
                    "data_gap": "所需日期没有完整数据，要改查哪个时间？",
                }.get(turn.status, "下面哪一种更接近你的想法？")
            sections.append(question)
            if turn.clarification.field in {"time", "comparison_time"}:
                start = turn.available_dates.start
                end = turn.available_dates.end_exclusive - timedelta(days=1)
                if growth:
                    if end >= start:
                        sections.append(
                            f"最近连续完整出库记录：{start} 至 {end}。"
                            "同比／环比还需对应比较期完整，不能只看本期是否在此范围内。"
                        )
                    else:
                        sections.append("当前尚无可用于品种比较的完整出库记录。")
                elif selected and selected.fields.get("domain") == "inventory":
                    sections[-1] = "目前只有备份库存，要查看这份快照吗？"
                    sections.append(f"可用库存快照日期：{start}，不是实时或当日日末库存。")
                else:
                    date_hint = f"{start} 至 {end}" if end >= start else "暂无完整日期"
                    if turn.clarification.kind in {"missing", "data_gap"}:
                        sections[-1] = (
                            "所需日期没有完整数据，要改查哪个时间？"
                            if turn.status == "data_gap"
                            else "你想和哪段时间比较？"
                            if turn.clarification.field == "comparison_time"
                            else "你想查哪段时间？"
                        )
                    sections.append(f"可用完整日期：{date_hint}。")
                    if selected and selected.fields.get("operation") == "comparison":
                        sections[-1] += "比较期需等长且不重叠。"
        choices = numbered_choices(turn)
        if choices:
            numbered = payload.get("number_reply_allowed", True)
            suggestions = [
                (f"{index}. " if numbered else "• ") + choice.label
                for index, choice in enumerate(choices, 1)
            ]
            sections.append("\n".join(suggestions))
    reply = "再次 @我，用文字补充即可。"
    if turn and numbered_choices(turn) and payload.get("number_reply_allowed", True):
        reply = "再次 @我 回复编号，以最新一张提问卡片为准；都不是，也可以直接用文字说明。"
    sections.append(reply)
    if "conversation_round" in payload:
        sections.insert(0, f"本次对话第 {round_count(payload)}/{MAX_CONVERSATION_ROUNDS} 轮")
    details.append(
        "已明确的条件会保留，不用重写整个问题。上下文保留30分钟，连续会话最多5轮"
        "（首次提问和结果后的追问都计入）；发送“重新开始”开启新话题。"
    )
    elements = [text(section) for section in sections]
    elements.append(panel("查看已保留的条件与说明", "\n\n".join(details)))
    card = {
        "schema": "2.0",
        "config": {"width_mode": "fill", "summary": {"content": title}},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
        "body": {"padding": "12px", "vertical_spacing": "8px", "elements": elements},
    }
    return {"semantic_status": payload["status"], "reply_card": card}, "\n\n".join(sections)


def coverage_text(report):
    """Use each loaded domain's actual coverage and respect inventory scope."""
    domains = executable_domains(report)
    lines = ["当前可查询的数据范围"]
    for domain in domains:
        if domain == "inventory":
            tz = ZoneInfo(report.operating.policy.business_timezone)
            instant = report.operations.inventory.as_of.astimezone(tz)
            lines.append(
                f"库存：{instant:%Y-%m-%d %H:%M:%S}（{tz.key}）备份时点，非实时或日末库存。"
            )
            continue
        start, end = available_dates(report) if domain == "sales" else domain_dates(report, domain)
        value = f"{start} 至 {end - timedelta(days=1)}" if end > start else "暂无完整日期"
        lines.append(f"{DOMAIN_LABELS[domain]}：{value}")
    missing = [label for domain, label in DOMAIN_LABELS.items() if domain not in domains]
    if missing:
        lines.append("当前快照或授权范围尚不可查：" + "、".join(missing))
    lines.append("当前为历史备份。未覆盖的日期不会当成零，也不会自动替换你指定的日期。")
    lines.append(
        "销售按订单创建日期统计有效订单金额，非支付成交额、未扣退款；退货和出库按各自业务日期统计。"
    )
    return "\n".join(lines)


def help_text(report):
    start, end = available_dates(report)
    fallback = f"\n模型暂不可用时，可使用固定指令：{start}至{start} 销售概览" if end > start else ""
    return (
        "在本群 @我，直接说你想了解的经营情况，可以先说一个大致目标，不需要按固定格式提问。\n"
        "我会展示已理解的需求，缺信息时每次只问一个关键问题。\n\n"
        + coverage_text(report)
        + "\n\n收到引导后，再次 @我 补充日期、指标或想修改的条件即可；不用重复整个问题。"
        "建议只是可选方向，不会未经确认删除你的筛选条件或改查其他指标。"
        "\n也可以同时提出多个目标；暂不支持的计算或条件会单独说明。"
        "\n上下文保留30分钟，连续会话最多5轮（首次提问和结果后的追问都计入）。"
        "第5轮处理后结束，下一条完整问题开启新会话。"
        "@我 发送“重新开始”可提前开启新话题，发送“数据范围”查看可用日期；这些操作不占分析轮次。"
        "\n“药品”泛称按当前商品范围统计，未按药品类别筛选；“卖得好”默认销售金额前10。"
    ) + fallback
