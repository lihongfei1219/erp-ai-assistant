from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.analysis.sales_query import _label
from app.schemas.analytics import AnalysisResponse


def _blocks(response: AnalysisResponse, row_limit: int) -> list[str]:
    blocks = ["本次问题理解\n" + "\n".join(response.interpretation)]
    for step, result in zip(response.plan.steps, response.results, strict=True):
        text = [
            f"{result.title} · {step.start_date} 至 {step.end_date_exclusive - timedelta(days=1)}"
        ]
        text.extend(result.findings)
        if result.kind == "comparison":
            rate = result.totals["change_rate"]
            text.append(
                "变化率："
                + (f"{Decimal(rate) * 100:.2f}%" if rate is not None else "无定义（比较期为零）")
            )
            text.append(f"其余维度变化金额：{result.totals['other_delta']}")
        for index, row in enumerate(result.rows[:row_limit], 1):
            fields = [
                f"{label}：{_label(str(row[key]), 100) if row.get(key) is not None else '—'}"
                for key, label in result.columns.items()
            ]
            if "code" in row and "code" not in result.columns:
                fields.append("编码：" + _label(str(row["code"]), 80))
            evidence = row.get("evidence_ids", [])
            if evidence:
                fields.append(
                    "订单依据ID："
                    + "、".join(str(x) for x in evidence[:3])
                    + (f"（共{row.get('evidence_count', len(evidence))}张）")
                )
            text.append(f"{index}. " + "；".join(fields))
        if len(result.rows) > row_limit:
            text.append(
                f"卡片节选前 {row_limit} 行，共 {len(result.rows)} 行；"
                "可缩短日期或减少排行条数查看。"
            )
        elif not result.rows:
            text.append("没有满足条件的记录。")
        text.extend(result.notes)
        blocks.append("\n".join(text))
    p = response.provenance
    source = "合成样例" if p.source_kind == "synthetic" else "历史备份"
    scope = "全平台采购企业" if p.scope.all_buyers else "当前授权采购企业范围"
    blocks.append(
        f"{source} · {scope} · {p.currency}\n"
        f"数据截至 {p.source_as_of.astimezone(ZoneInfo(p.business_timezone)):%Y-%m-%d %H:%M} "
        f"（{p.business_timezone}）\n"
        f"销售纳入状态：{'、'.join(p.included_statuses)}；其他业务口径见各项说明；非资金收付，销售未扣退款。\n"
        f"规则版本：{p.policy_id}；查询编号：{response.run_id}\n"
        "30分钟内可继续 @我 追问；发送“清除追问上下文”重新开始。"
    )
    if response.warnings:
        blocks.append(
            "数据说明：\n" + "\n".join(_label(text, 240) for text in response.warnings[:5])
        )
    return blocks
