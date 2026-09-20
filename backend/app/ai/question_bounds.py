"""Independent, conservative grounding for the model's supported query vocabulary."""
import re
from datetime import date, timedelta

from app.analysis.sales_query import QueryRequest, QueryUnavailable

_ISO_RANGE = re.compile(r'(\d{4}-\d{2}-\d{2})\s*(?:至|到|~|～)\s*(\d{4}-\d{2}-\d{2})')
_CN_RANGE = re.compile(
    r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]?\s*(?:至|到)\s*'
    r'(?:(\d{4})年)?(?:(\d{1,2})月)?(\d{1,2})[日号]?')
_RELATIVE = re.compile(r'最近一周|过去一周|之前一周|上周|昨天|前天|'
                       r'(?:最近|过去)(?:[0-9]{1,3}|[一二三四五六七八九十两]+)天')
_COUNT = re.compile(r'(?:前|top\s*)([0-9]+|[一二三四五六七八九十两]+)(?:名|个)?|'
                   r'([0-9]+|[一二三四五六七八九十两]+)(?:个|名)(?=商品|产品)', re.I)
# Unknown residue can be an entity, filter, instruction or unsupported metric.
# Reject it instead of letting a model silently turn it into a platform-wide query.
_WORDS = sorted({
    '请', '请问', '帮我', '帮忙', '麻烦', '能不能', '能否', '可以', '给我', '我想', '想看',
    '查询', '查一下', '查看', '看看', '看一下', '统计一下', '统计', '分析一下', '分析',
    '汇总一下', '汇总', '列一下', '列出', '列', '把', '一下', '的', '按', '以', '在', '从',
    '这段时间', '这几天', '期间', '一共', '总共', '整体', '全平台', '平台整体', '平台',
    '销售概览', '销售表现', '经营数据', '经营情况', '总体', '整体销售', '销售情况',
    '销售额', '销售金额', '销售总额', '总销售额', '金额', '订单数', '订单', '单数',
    '商品', '产品', '销售', '生意', '表现', '情况', '数据', '怎么样', '如何', '是多少',
    '卖了多少钱', '卖了多少', '多少钱', '哪些', '哪个', '什么', '有', '最', '比较',
    '热销', '卖得好', '卖得最好', '卖得比较好', '排行', '排行榜', '排名', '排序',
    '降序', '前', '每日', '每天', '日', '趋势', '走势', '变化', '明细', '明白',
    '总额', '多少', '呢', '吗', '吧', '下',
}, key=len, reverse=True)
_RESIDUE = re.compile('|'.join(re.escape(word) for word in _WORDS))


def _number(value):
    if value.isascii() and value.isdigit():
        return int(value)
    digits = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
              '六': 6, '七': 7, '八': 8, '九': 9}
    if value == '十':
        return 10
    if '十' in value:
        left, right = value.split('十')
        return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)
    return digits[value]


def question_bounds(question: str, today: date) -> QueryRequest:
    """Establish dates/count independently and refuse unexplained query conditions."""
    try:
        match = _ISO_RANGE.search(question)
        if match:
            start = date.fromisoformat(match[1])
            end = date.fromisoformat(match[2]) + timedelta(days=1)
        elif match := _CN_RANGE.search(question):
            year, month = int(match[1] or today.year), int(match[2])
            start = date(year, month, int(match[3]))
            end = date(int(match[4] or year), int(match[5] or month), int(match[6]))
            end += timedelta(days=1)
        elif match := _RELATIVE.search(question):
            period = match[0]
            if period == '上周':
                end = today - timedelta(days=today.weekday())
                start = end - timedelta(days=7)
            elif period in {'昨天', '前天'}:
                start = today - timedelta(days=1 if period == '昨天' else 2)
                end = start + timedelta(days=1)
            else:
                days = 7 if period.endswith('周') else _number(period[2:-1])
                start, end = today - timedelta(days=days), today
        else:
            raise ValueError('No supported date expression')
        remainder = question[:match.start()] + question[match.end():]
        counts = list(_COUNT.finditer(remainder))
        if len(counts) > 1:
            raise ValueError('Ambiguous ranking limit')
        top = _number(counts[0][1] or counts[0][2]) if counts else 10
        remainder = _COUNT.sub('', remainder)
        remainder = _RESIDUE.sub('', remainder)
        if re.sub(r'[\s，。！？、：；,.!?:;（）()]+', '', remainder):
            raise ValueError('Unrecognized constraint')
        metric = 'orders' if '订单数' in question or '单数' in question else 'amount'
        return QueryRequest(start_date=start, end_date_exclusive=end, top_n=top, metric=metric)
    except (ValueError, KeyError, OverflowError):
        raise QueryUnavailable('请明确完整日期，并仅查询全平台销售概览、商品排行或每日趋势。'
                               '当前不支持指定客户、商品或地区筛选；排行限前 1—50 名。') from None


def validate_grounding(question: str, bounds: QueryRequest, intent):
    if (intent.start_date != bounds.start_date
            or intent.end_date_exclusive != bounds.end_date_exclusive
            or intent.top_n != bounds.top_n
            or (intent.tool == 'product_ranking' and intent.metric != bounds.metric)):
        raise QueryUnavailable('解析结果与问题中的日期或排行要求不一致，请用帮助中的固定指令重试。')
    rank = bool(re.search(
        r'排行|排名|排序|热销|卖得|前[0-9一二三四五六七八九十]|商品|产品', question))
    trend = bool(re.search(r'每日|每天|趋势|走势', question))
    expected = 'sales_trend' if trend else 'product_ranking' if rank else 'sales_summary'
    if (rank and trend) or intent.tool != expected:
        raise QueryUnavailable('分析目标尚不明确，请一次选择销售概览、商品排行或每日趋势。')
