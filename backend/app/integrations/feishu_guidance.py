"""Present structured dialogue decisions; never interpret or rewrite customer intent."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from app.analysis.analytics import available_dates
from app.analysis.operations import DOMAIN_LABELS, domain_dates, executable_domains
from app.integrations.feishu_cards import _text, information_card
from app.semantic.dialogue_schemas import DialogueTurn


def numbered_choices(turn: DialogueTurn):
    # Keep exactly the same sequence for display and bound numeric replies.
    return [choice for choice in turn.choices if choice.action.kind != "date_range"]


def render_guidance(payload):
    turn = (
        DialogueTurn.model_validate(payload["dialogue_turn"])
        if payload.get("dialogue_turn") else None
    )
    title = {
        "needs_input": "再补充一点，就能继续",
        "capability_gap": "这部分暂时做不到，一起调整需求",
        "data_gap": "所需数据暂未覆盖，请确认范围",
    }.get(payload["status"], "继续完善分析")
    sections = []
    if payload.get("notice"):
        title = "请用文字说明你的选择"
        sections.append(payload["notice"])
    if turn is not None:
        if turn.understood_summary:
            sections.append("已理解的需求\n" + turn.understood_summary)
        if turn.applied_defaults:
            sections.append("当前采用的默认条件（可以修改）\n" + "；".join(turn.applied_defaults))
        if turn.repeated_clarification:
            sections.append("前面已明确的条件仍然保留，不用重写整个问题；只需说明本轮要补充或改动的部分。")
        if turn.clarification is not None:
            sections.append("本轮需要确认\n" + turn.clarification.question)
            if turn.clarification.field in {"time", "comparison_time"}:
                selected = next(
                    (
                        item for item in turn.draft.intents
                        if item.id == turn.clarification.intent_id
                    ),
                    None,
                )
                start = turn.available_dates.start
                end = turn.available_dates.end_exclusive - timedelta(days=1)
                if selected and selected.fields.get("domain") == "inventory":
                    sections.append(
                        f"可用库存快照日期：{start}。仅代表备份时点，不是实时或当日日末库存。"
                        "\n你可以说明是否接受查询这个备份时点；不会自动替换你原来要求的日期。"
                    )
                else:
                    label = selected.label if selected else "本轮目标"
                    date_hint = (
                        f"{start} 至 {end}" if end >= start else "暂无完整日期"
                    )
                    sections.append(
                        f"{label}可用完整日期：{date_hint}。"
                        "\n可以直接说某一天或起止日期。"
                    )
                    if selected and selected.fields.get("operation") == "comparison":
                        sections[-1] += "比较分析需要等长且不重叠的两段日期。"
        choices = numbered_choices(turn)
        if choices:
            numbered = payload.get("number_reply_allowed", True)
            suggestions = [
                (f"{index}. " if numbered else "• ") + choice.label
                for index, choice in enumerate(choices, 1)
            ]
            sections.append("也可以选择以下建议\n" + "\n".join(suggestions))
    reply = (
        "在本群再次 @我，用自己的话补充或修改即可，不必照抄建议。"
        "如果我的理解不对，直接指出要修改的地方。"
    )
    if turn and numbered_choices(turn) and payload.get("number_reply_allowed", True):
        reply += "\n只有一轮待答建议时，也可 @我 回复编号；多轮建议请用文字说明选择。"
    reply += "\n上下文保留30分钟；想开始新话题，可 @我 发送“重新开始”。"
    sections.append("如何继续\n" + reply)
    card = information_card(title, "")
    card["elements"] = []
    for index, section in enumerate(sections):
        if index:
            card["elements"].append({"tag": "hr"})
        card["elements"].append(_text(section))
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
    lines.append("销售按订单创建日期统计有效订单金额，非支付成交额、未扣退款；退货和出库按各自业务日期统计。")
    return "\n".join(lines)


def help_text(report):
    start, end = available_dates(report)
    fallback = (
        f"\n模型暂不可用时，可使用固定指令：{start}至{start} 销售概览"
        if end > start else ""
    )
    return (
        "在本群 @我，直接说你想了解的经营情况，可以先说一个大致目标，不需要按固定格式提问。\n"
        "我会展示已理解的需求，缺信息时每次只问一个关键问题。\n\n"
        + coverage_text(report)
        + "\n\n收到引导后，再次 @我 补充日期、指标或想修改的条件即可；不用重复整个问题。"
        "建议只是可选方向，不会未经确认删除你的筛选条件或改查其他指标。"
        "\n也可以同时提出多个目标；暂不支持的计算或条件会单独说明。"
        "\n30分钟内可继续追问；@我 发送“重新开始”开启新话题，发送“数据范围”查看可用日期。"
        "\n“药品”泛称按当前商品范围统计，未按药品类别筛选；“卖得好”默认销售金额前10。"
    ) + fallback
