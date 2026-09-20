"""Locally calculated overview and daily trend cards, with no generated facts."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from app.analysis.sales_insights import SalesTrendResult
from app.analysis.sales_query import _label
from app.integrations.feishu_cards import _columns, _money, _note, _text


def insight_text(result) -> str:
    end = result.request.end_date_exclusive - timedelta(days=1)
    return (f'{result.request.start_date} 至 {end} · 全平台有效销售\n'
            f'销售额 {result.currency} {_money(result.total_amount)}\n'
            f'{result.order_count} 单 · {result.buyer_count} 家采购企业 · '
            f'{result.product_count} 种商品\n'
            '按订单创建日期；非支付成交额，未扣退款。')


def insight_card(result, query_id: str) -> dict:
    trend = isinstance(result, SalesTrendResult)
    currency = '¥' if result.currency == 'CNY' else _label(result.currency, 3)
    def amount(value):
        return f'{currency} {_money(value)}'
    end = result.request.end_date_exclusive - timedelta(days=1)
    elements = [
        _text(f'{result.request.start_date:%Y.%m.%d} — {end:%Y.%m.%d}\n全平台 · 完整区间'),
        {'tag': 'hr'},
        _columns(
            (2, [_text(f'有效销售额\n**{amount(result.total_amount)}**', markdown=True)]),
            (1, [_text(f'订单数\n**{result.order_count:,} 单**', markdown=True)]),
            (1, [_text(f'采购企业\n**{result.buyer_count:,} 家**', markdown=True)]),
        ),
        {'tag': 'hr'},
    ]
    if trend:
        elements.append(_text('**每日销售额**', markdown=True))
        maximum = max((point.order_amount for point in result.daily), default=0)
        # One plain-text block per week keeps long date ranges within card size limits.
        for offset in range(0, len(result.daily), 7):
            lines = []
            for point in result.daily[offset:offset + 7]:
                width = round(max(0, point.order_amount) / maximum * 12) if maximum > 0 else 0
                lines.append(f'{point.day:%m-%d}  {amount(point.order_amount)}  ·  '
                             f'{point.order_count} 单 / {point.buyer_count} 家\n'
                             + ('━' * width if width else '·'))
            elements.append(_text('\n'.join(lines)))
        if maximum > 0:
            peak = next(point for point in result.daily if point.order_amount == maximum)
            ties = sum(point.order_amount == maximum for point in result.daily)
            suffix = f'（共 {ties} 日并列，展示最早日）' if ties > 1 else ''
            elements.append(_text(f'单日最高：{peak.day:%m-%d}，{amount(maximum)}{suffix}。'))
        elements.append(_note('采购企业按日去重；区间采购企业数不等于每日相加。'))
    else:
        average = (amount(result.average_order_amount)
                   if result.average_order_amount is not None else '—')
        elements.extend([
            _columns((1, [_text(f'平均每单金额\n{average}')]),
                     (1, [_text(f'成交商品\n{result.product_count:,} 种')]),),
            _note(f'本期另有 {result.excluded_order_count:,} 单未纳入有效销售口径。'),
        ])
    if not result.order_count:
        elements.append(_text('该完整区间内无有效销售订单。'))
    local = result.source_as_of.astimezone(ZoneInfo(result.business_timezone))
    source = '演示样例' if result.source_kind == 'synthetic' else '历史备份'
    elements.extend([{'tag': 'hr'}, _note(
        f'{source} · 数据截至 {local:%Y-%m-%d %H:%M}（{result.business_timezone}）\n'
        f'按订单创建日期；纳入{"、".join(result.included_statuses)}；非支付成交额，未扣退款。\n'
        f'查询编号 {_label(query_id, 32)}')])
    return {'config': {'wide_screen_mode': True},
            'header': {'template': 'blue' if trend else 'turquoise',
                       'title': {'tag': 'plain_text',
                                 'content': '每日销售趋势' if trend else '销售概览'}},
            'elements': elements}
