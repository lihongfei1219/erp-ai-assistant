"""Independent grounding of supported questions, without trusting model omissions."""

import re
from datetime import date, timedelta

from app.ai.analytics_language import normalize_question
from app.ai.question_bounds import _CN_RANGE, _COUNT, _ISO_RANGE, _RELATIVE, _WORDS, _number
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisPlan, AnalysisQuestion

WORDS = sorted(
    set(_WORDS)
    | {
        "客户",
        "采购企业",
        "企业数",
        "平均订单金额",
        "平均",
        "概览",
        "环比",
        "日波动线索",
        "波动",
        "异常",
        "线索",
        "检测",
        "对比",
        "比较期",
        "本期",
        "上期",
        "拆解",
        "贡献",
        "下降",
        "增长",
        "增减",
        "原因",
        "与",
        "和",
        "及",
        "并",
        "并且",
        "同时",
        "再看",
        "再",
        "然后",
        "继续",
        "前日",
        "商品种数",
        "采购",
        "企业",
        "变化贡献",
        "金额变化",
        "有效",
        "图表",
        "绘制",
        "画",
        "折线图",
        "柱状图",
        "展示",
        "呈现",
        "分别",
        "这两个期间",
        "谢谢",
        "帮我看下",
        "帮我看看",
        "麻烦你",
        "换成",
        "改成",
        "改为",
        "还是",
        "取",
        "显示",
    },
    key=len,
    reverse=True,
)
RESIDUE = re.compile("|".join(map(re.escape, WORDS)))
RANK = re.compile(r"排行|排名|排序|热销|卖得|前[0-9一二三四五六七八九十]|top", re.I)


def _intervals(question: str, today: date):
    matches = []
    for pattern in (_ISO_RANGE, _CN_RANGE, _RELATIVE):
        for match in pattern.finditer(question):
            if any(match.start() < end and match.end() > start for start, end, _ in matches):
                continue
            if pattern is _ISO_RANGE:
                dates = (
                    date.fromisoformat(match[1]),
                    date.fromisoformat(match[2]) + timedelta(days=1),
                )
            elif pattern is _CN_RANGE:
                year, month = int(match[1] or today.year), int(match[2])
                dates = (
                    date(year, month, int(match[3])),
                    date(int(match[4] or year), int(match[5] or month), int(match[6]))
                    + timedelta(days=1),
                )
            elif match[0] == "上周":
                end = today - timedelta(days=today.weekday())
                dates = (end - timedelta(days=7), end)
            elif match[0] in {"昨天", "前天"}:
                start = today - timedelta(days=1 if match[0] == "昨天" else 2)
                dates = (start, start + timedelta(days=1))
            else:
                count = 7 if match[0].endswith("周") else _number(match[0][2:-1])
                dates = (today - timedelta(days=count), today)
            matches.append((match.start(), match.end(), dates))
    matches.sort()
    remainder = question
    for start, end, _ in reversed(matches):
        remainder = remainder[:start] + " " + remainder[end:]
    return [dates for _, _, dates in matches], remainder


def validate_question_plan(body: AnalysisQuestion, plan: AnalysisPlan, today: date) -> None:
    """Reject unclear conditions instead of accepting a smaller plausible question."""
    question = normalize_question(body.question)
    try:
        intervals, remainder = _intervals(question, today)
        counts = [_number(match[1] or match[2]) for match in _COUNT.finditer(remainder)]
        if len(set(counts)) > 1:
            raise ValueError("ambiguous limits")
        residue = RESIDUE.sub("", _COUNT.sub("", remainder))
        if re.sub(r"[\s，。！？、：；,.!?:;（）()]+", "", residue):
            raise ValueError("unsupported conditions")
        if not intervals:
            if body.previous_plan is None:
                raise ValueError("missing dates")
            # A follow-up must refer to a single unambiguous current interval.
            intervals = list(
                dict.fromkeys(
                    (s.start_date, s.end_date_exclusive) for s in body.previous_plan.steps
                )
            )
            if len(intervals) != 1:
                raise ValueError("ambiguous previous dates")
        if len(intervals) > 2:
            raise ValueError("ambiguous periods")
        if re.search(r"环比|同比|比较期|对比|贡献|拆解|原因", remainder) and len(intervals) != 2:
            raise ValueError("comparison needs two explicit intervals")
        previous = body.previous_plan.steps if body.previous_plan else []
        rank = bool(RANK.search(remainder)) or (
            len(previous) == 1
            and previous[0].kind in {"product_ranking", "buyer_ranking"}
            and bool(re.search(r"换成|改成|改为", remainder))
            and bool(re.search(r"订单数|单数|金额|销售额", remainder))
            and not re.search(r"概览|汇总|趋势|走势|波动|异常|比较|对比", remainder)
        )
        comparison = len(intervals) == 2 and bool(re.search(r"比较|对比|贡献|拆解", remainder))
        anomaly = bool(re.search(r"波动|异常", remainder))
        expected = set()
        if comparison:
            expected.add("comparison")
        if anomaly:
            expected.add("anomalies")
        if not anomaly and re.search(r"每日|每天|趋势|走势|折线图", remainder):
            expected.add("trend")
        if rank:
            if re.search(r"客户|企业", remainder):
                expected.add("buyer_ranking")
            if re.search(r"商品|产品", remainder):
                expected.add("product_ranking")
            if not re.search(r"商品|产品|客户|企业", remainder):
                previous = body.previous_plan.steps if body.previous_plan else []
                if len(previous) == 1 and previous[0].kind in {"product_ranking", "buyer_ranking"}:
                    expected.add(previous[0].kind)
                elif "热销" in remainder:
                    expected.add("product_ranking")
        if (
            re.search(r"概览|汇总|总体|整体|平均订单金额|企业数|商品种数", remainder)
            or not expected
        ):
            expected.add("summary")
        if {step.kind for step in plan.steps} != expected:
            raise ValueError("changed tools")
        if len(plan.steps) != len(expected):
            raise ValueError("duplicate tools")
        previous = body.previous_plan.steps if body.previous_plan else []
        inherited = (
            previous[0]
            if len(previous) == 1
            and len(plan.steps) == 1
            and previous[0].kind == plan.steps[0].kind
            else None
        )
        metric = (
            "orders"
            if re.search(r"订单数|单数", remainder)
            else "amount"
            if re.search(r"金额|销售额|热销", remainder)
            else inherited.metric
            if inherited
            else "amount"
        )
        top_n = counts[0] if counts else inherited.top_n if inherited else 10
        for step in plan.steps:
            if (step.start_date, step.end_date_exclusive) != intervals[0]:
                raise ValueError("changed dates")
            if step.kind == "comparison":
                if (
                    len(intervals) != 2
                    or (step.comparison_start_date, step.comparison_end_date_exclusive)
                    != intervals[1]
                ):
                    raise ValueError("changed comparison")
                dimension = "buyer" if re.search(r"客户|企业", remainder) else "product"
                if step.dimension != dimension:
                    raise ValueError("changed dimension")
            if step.kind in {"buyer_ranking", "product_ranking", "trend"} and step.metric != metric:
                raise ValueError("changed metric")
            if step.kind in {"buyer_ranking", "product_ranking", "comparison"}:
                if step.top_n != top_n:
                    raise ValueError("changed limit")
        if len(intervals) == 2 and not comparison:
            raise ValueError("unsupported multi-period request")
    except (ValueError, KeyError, OverflowError):
        raise QueryUnavailable(
            "问题中的日期、指标或筛选条件尚不能完整核验。请明确日期及销售概览、每日趋势、"
            "客户/商品排行、期间比较或日波动；指定企业、商品、地区及利润等筛选暂不支持。"
            "也可使用手动分析明确参数。"
        ) from None
