import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.analysis.sales import analyze_sales
from app.analysis.sales_query import QueryRequest, product_ranking
from app.core.business_rules import load_business_rules
from app.integrations.feishu_app import reply_card
from app.integrations.feishu_cards import ranking_card
from tests.unit.test_feishu_sales import bot_for


@pytest.fixture
def ranking(extract, window, scope, source_as_of):
    report = analyze_sales(extract, window, scope, source_as_of=source_as_of,
                           rules=load_business_rules(), synthetic=True)
    return product_ranking(report, QueryRequest(start_date=date(2026, 9, 1),
                                               end_date_exclusive=date(2026, 9, 3)))


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def test_card_exposes_summary_and_ranking_without_debug_details(ranking):
    card = ranking_card(ranking, 'trace-safe')
    payload = json.dumps(card, ensure_ascii=False)
    assert card['header']['template'] == 'blue'
    assert '300.00' in payload and '260.00' in payload
    assert '2026.09.01' in payload and '2026.09.02' in payload
    assert '采购企业' in payload and '86.67%' in payload
    assert 'trace-safe' in payload
    assert ranking.policy_fingerprint not in payload
    assert ranking.metric_version not in payload
    assert 'order_ids' not in payload


def test_names_remain_plain_text_and_do_not_create_mentions_or_links(ranking):
    malicious = '<at id=all></at> [click](https://example.com) **bold**\nother'
    item = ranking.items[0].model_copy(update={'product_name': malicious})
    result = ranking.model_copy(update={'items': [item]})
    nodes = list(walk(ranking_card(result, 'q')))
    assert not any(malicious in n.get('content', '') for n in nodes)
    assert all('example.com' not in n.get('content', '')
               for n in nodes if n.get('tag') == 'lark_md')
    assert any('click' in n.get('content', '') for n in nodes if n.get('tag') == 'plain_text')


def test_empty_and_order_sorted_cards_are_truthful(ranking):
    empty = ranking.model_copy(update={'items': [], 'total_amount': 0,
                                       'order_count': 0, 'buyer_count': 0})
    assert '无有效销售' in json.dumps(ranking_card(empty, 'q'), ensure_ascii=False)
    orders = ranking.model_copy(update={'request': ranking.request.model_copy(
        update={'metric': 'orders'})})
    payload = json.dumps(ranking_card(orders, 'q'), ensure_ascii=False)
    assert '按订单数' in payload
    assert '销售额前三' not in payload


def test_card_reply_uses_interactive_and_original_dedup_id():
    requests = []
    def reply(request):
        requests.append(request)
        return SimpleNamespace(code=0, data=SimpleNamespace(message_id='om_card'))
    client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        message=SimpleNamespace(reply=reply))))
    card = {'header': {'title': {'tag': 'plain_text', 'content': '商品排行'}}, 'elements': []}
    reply_card(client, 'om_original', card, 'stable-key')
    assert requests[0].message_id == 'om_original'
    assert requests[0].request_body.msg_type == 'interactive'
    assert requests[0].request_body.uuid == 'stable-key'
    assert json.loads(requests[0].request_body.content) == card


def test_persisted_card_is_delivered_without_recomputing(ranking):
    bot, store = bot_for()
    card = ranking_card(ranking, 'q')
    store.enqueue({'app_id': 'cli_test', 'tenant_key': 'tenant-test', 'chat_id': 'oc_test',
                   'user_open_id': 'ou_user', 'message_id': 'om_test'}, {'kind': 'ranking'})
    job = store.claim()
    store.finish_analysis(job, {'reply_card': card}, 'fallback')
    sent = []
    assert bot.process_pending(lambda *args: pytest.fail('text instead of card'),
                               card_sender=lambda *args: sent.append(args)) == 1
    assert sent[0][1] == card
    assert store.rows[0]['status'] == 'sent'


def test_fifty_long_names_fit_card_payload_budget(ranking):
    items = [ranking.items[0].model_copy(update={
        'product_name': '很长的商品名称与规格' * 20, 'product_code': f'SKU-{i}'})
        for i in range(50)]
    result = ranking.model_copy(update={'items': items,
                                       'request': ranking.request.model_copy(update={'top_n': 50})})
    encoded = json.dumps(ranking_card(result, 'q'), ensure_ascii=False).encode('utf-8')
    assert len(encoded) < 28000


def test_same_display_name_keeps_sku_identity_visible(ranking):
    result = ranking.model_copy(update={'items': [
        item.model_copy(update={'product_name': '同名商品'}) for item in ranking.items]})
    payload = json.dumps(ranking_card(result, 'q'), ensure_ascii=False)
    assert 'SKU-A' in payload and 'SKU-B' in payload
