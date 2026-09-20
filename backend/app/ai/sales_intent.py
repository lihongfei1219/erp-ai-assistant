"""Validate model candidates as strictly as local commands."""
import json
import re
from datetime import date, timedelta
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.analysis.sales_query import QueryRequest, QueryUnavailable, parse_question
from app.schemas.sales import StrictModel


class UnrecognizedQuestion(QueryUnavailable):
    """A question that may be offered to the configured interpreter."""


class SalesIntent(QueryRequest):
    tool: Literal['product_ranking', 'sales_summary', 'sales_trend']

    @field_validator('start_date', 'end_date_exclusive', mode='before')
    @classmethod
    def explicit_date(cls, value):
        if type(value) is date:
            return value
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Use an explicit ISO date')
        return value

    @model_validator(mode='after')
    def parameters_match_tool(self):
        if self.tool != 'product_ranking' and (self.metric != 'amount' or self.top_n != 10):
            raise ValueError('Ranking options only apply to product_ranking')
        return self

    def to_query(self) -> QueryRequest:
        return QueryRequest.model_validate(self.model_dump(exclude={'tool'}))


class ModelDecision(StrictModel):
    action: Literal['query', 'clarify']
    intent: SalesIntent | None
    unsupported_conditions: list[str] = Field(max_length=20)


def check_question(text: str):
    if not text.strip() or len(text) > 1000:
        raise QueryUnavailable('请将问题控制在 1—1000 字，并明确一个查询目标。')
    if '药品' in text:
        raise QueryUnavailable('药品分类尚未建立；如需全部商品，请明确询问商品销售额排行。')
    if any(word in text for word in ('销量', '数量', '盒数', '瓶数')):
        raise QueryUnavailable('数量单位尚未统一，请改用销售额或订单数。')
    if re.search(r'利润|退款|退货|库存|支付|回款|净额|同比|环比|对比|相比|比较(?!好)|'
                 r'只看|仅看|限定|筛选|密钥|文件|数据库|https?://|\bSQL\b', text, re.I):
        raise QueryUnavailable('当前支持全平台销售概览、商品排行和每日趋势；'
                               '暂不支持该问题中的筛选、比较或其他指标。')


_PERIOD = (r'最近一周|之前一周|上周|昨天|前天|最近[0-9]{1,3}天|'
           r'[0-9]{4}-[0-9]{2}-[0-9]{2}\s*至\s*[0-9]{4}-[0-9]{2}-[0-9]{2}')
_INSIGHT = re.compile(
    r'(?:请)?(?:帮我)?(?:查询|查一下|看一下|看看|统计一下|统计)?\s*'
    r'(?P<period>' + _PERIOD + r')\s*的?\s*'
    r'(?P<topic>销售概览|销售怎么样|销售情况|一共卖了多少钱|销售额是多少|总销售额|销售总额|'
    r'每日销售趋势|每日销售额|每天销售情况|每天卖了多少钱)[？?。]?')


def parse_local_intent(text: str, today: date) -> SalesIntent:
    check_question(text)
    match = _INSIGHT.fullmatch(text.strip())
    if match:
        period = match['period']
        try:
            if period in {'昨天', '前天'}:
                start = today - timedelta(days=1 if period == '昨天' else 2)
                request = QueryRequest(start_date=start,
                                       end_date_exclusive=start + timedelta(days=1))
            elif re.fullmatch(r'最近[0-9]{1,3}天', period):
                request = QueryRequest(start_date=today - timedelta(days=int(period[2:-1])),
                                       end_date_exclusive=today)
            else:
                request = parse_question(period + ' 商品销售额排行', today)
            tool = 'sales_trend' if match['topic'].startswith(('每日', '每天')) else 'sales_summary'
            return SalesIntent(**request.model_dump(), tool=tool)
        except (ValueError, OverflowError):
            raise QueryUnavailable('日期范围无效，请选择 1—90 个完整自然日。') from None
    try:
        request = parse_question(text, today)
        return SalesIntent(**request.model_dump(), tool='product_ranking')
    except QueryUnavailable:
        raise UnrecognizedQuestion('请明确统计日期及目标，例如“2026-09-09至2026-09-15 销售概览”。'
                                   '发送“帮助”查看支持的分析。') from None


def parse_model_decision(content: str) -> SalesIntent:
    try:
        if len(content) > 12000:
            raise ValueError('too long')
        decision = ModelDecision.model_validate(json.loads(content))
    except (TypeError, ValueError, ValidationError):
        raise QueryUnavailable('模型未能给出有效查询条件，请明确日期和分析目标后重试。') from None
    if decision.action != 'query' or decision.intent is None or decision.unsupported_conditions:
        raise QueryUnavailable('这个问题需要补充日期或包含暂不支持的条件。'
                               '请明确全平台销售概览、商品排行或每日趋势及统计日期。')
    return decision.intent
