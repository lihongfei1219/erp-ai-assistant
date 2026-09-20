import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.analysis.sales import analyze_sales
from app.analysis.sales_insights import sales_summary, sales_trend
from app.analysis.sales_query import QueryRequest
from app.core.business_rules import load_business_rules
from app.integrations.feishu_insight_cards import insight_card
from app.schemas.sales import DailyPoint


@pytest.fixture
def report(extract, window, scope, source_as_of):
    return analyze_sales(extract, window, scope, source_as_of=source_as_of,
                         rules=load_business_rules(), synthetic=True)


def test_summary_keeps_real_metrics_and_source(report):
    result = sales_summary(report, QueryRequest(start_date='2026-09-01',
                                               end_date_exclusive='2026-09-04'))
    payload = json.dumps(insight_card(result, 'safe-query'), ensure_ascii=False)
    assert '300.00' in payload and '150.00' in payload
    assert '2026.09.03' in payload and 'safe-query' in payload
    assert '未扣退款' in payload and '演示样例' in payload
    assert result.policy_fingerprint not in payload


def test_ninety_days_fit_payload_budget_and_keep_every_date(report):
    result = sales_trend(report, QueryRequest(start_date='2026-09-01',
                                             end_date_exclusive='2026-09-04'))
    daily = [DailyPoint(day=date(2026, 6, 1) + timedelta(days=i),
                        order_amount=Decimal('1234567.89'), order_count=123, buyer_count=100)
             for i in range(90)]
    result = result.model_copy(update={'daily': daily})
    payload = json.dumps(insight_card(result, 'safe-query'), ensure_ascii=False)
    assert len(payload.encode('utf-8')) < 28000
    assert all(f'{point.day:%m-%d}' in payload for point in daily)
    assert '90 日并列' in payload


def test_zero_orders_shows_no_invented_average_or_peak(report):
    request = QueryRequest(start_date='2026-09-03', end_date_exclusive='2026-09-04')
    for result in [sales_summary(report, request), sales_trend(report, request)]:
        payload = json.dumps(insight_card(result, 'empty'), ensure_ascii=False)
        assert '无有效销售订单' in payload
        assert '单日最高' not in payload
