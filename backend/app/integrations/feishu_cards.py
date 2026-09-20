"""Compact Feishu cards; source labels are always rendered as plain text."""
from collections import Counter
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from app.analysis.sales_query import RankingResult, _label


def _money(value) -> str:
    return f"{Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _text(content: str, *, markdown: bool = False) -> dict:
    return {'tag': 'div', 'text': {'tag': 'lark_md' if markdown else 'plain_text',
                                  'content': content}}


def _columns(*columns: tuple[int, list[dict]]) -> dict:
    return {'tag': 'column_set', 'flex_mode': 'none',
            'columns': [{'tag': 'column', 'width': 'weighted', 'weight': weight,
                         'vertical_align': 'top', 'elements': elements}
                        for weight, elements in columns]}


def _note(content: str) -> dict:
    return {'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': content}]}


def information_card(title: str, text: str, *, warning: bool = False) -> dict:
    return {
        'config': {'wide_screen_mode': True},
        'header': {'template': 'orange' if warning else 'blue',
                   'title': {'tag': 'plain_text', 'content': title}},
        'elements': [_text(text)],
    }


def ranking_card(result: RankingResult, query_id: str) -> dict:
    request = result.request
    metric = '销售额' if request.metric == 'amount' else '订单数'
    end = request.end_date_exclusive - timedelta(days=1)
    currency = '¥' if result.currency == 'CNY' else _label(result.currency, 3)
    def amount(value):
        return f'{currency} {_money(value)}'
    actual_count = len(result.items)
    elements = [
        _text(f'{request.start_date:%Y.%m.%d} — {end:%Y.%m.%d}\n'
              f'全平台 · 按{metric}排序 · 完整区间'),
        {'tag': 'hr'},
        _columns(
            (2, [_text(f'有效销售额\n**{amount(result.total_amount)}**', markdown=True)]),
            (1, [_text(f'订单数\n**{result.order_count:,} 单**', markdown=True)]),
            (1, [_text(f'采购企业\n**{result.buyer_count:,} 家**', markdown=True)]),
        ),
        {'tag': 'hr'},
        _text(f'**商品排名 · TOP {actual_count}**', markdown=True),
    ]
    medals = ('🥇', '🥈', '🥉')
    names = Counter(_label(item.product_name or item.product_code, 46) for item in result.items)
    for index, item in enumerate(result.items, 1):
        rank = medals[index - 1] if index <= 3 else f'{index:02d}.'
        name = _label(item.product_name or item.product_code, 46)
        share = (f'{_money(item.amount_share * 100)}%'
                 if item.amount_share is not None else '—')
        if request.metric == 'amount':
            detail = f'{item.order_count} 单 · {item.buyer_count} 家采购 · 金额占比 {share}'
            primary = amount(item.amount)
        else:
            detail = f'{amount(item.amount)} · {item.buyer_count} 家采购 · 金额占比 {share}'
            primary = f'{item.order_count} 单'
        if names[name] > 1:
            detail = f'编码 {_label(item.product_code, 80)}\n{detail}'
        if actual_count > 10:
            # Keep all requested rows while staying within a conservative JSON budget.
            elements.append(_text(f'{rank}  {name}  ·  {primary}\n{detail}'))
        else:
            elements.append(_columns(
                (3, [_text(f'{rank}  {name}'), _note(detail)]),
                (2, [_text(f'**{primary}**', markdown=True)]),
            ))
    if not result.items:
        elements.append(_text('该期间无有效销售订单。可以换一个完整日期区间查询。'))
    elements.append({'tag': 'hr'})
    if request.metric == 'amount' and len(result.items) >= 3 and result.total_amount > 0:
        top_three = sum((item.amount for item in result.items[:3]), Decimal(0))
        elements.append(_text(
            f'前三名合计 {amount(top_three)}，占本期有效销售额 '
            f'{_money(top_three / result.total_amount * 100)}%。'))
    local = result.source_as_of.astimezone(ZoneInfo(result.business_timezone))
    source = '演示样例' if result.source_kind == 'synthetic' else '历史备份'
    elements.append(_note(
        f'{source} · 数据截至 {local:%Y-%m-%d %H:%M}（{result.business_timezone}）\n'
        f'按订单创建日期；纳入{"、".join(result.included_statuses)}。'
        f'金额非支付成交额，未扣退款。\n查询编号 {_label(query_id, 32)}'))
    return {
        'config': {'wide_screen_mode': True},
        'header': {'template': 'blue',
                   'title': {'tag': 'plain_text', 'content': f'商品{metric}排行'}},
        'elements': elements,
    }
