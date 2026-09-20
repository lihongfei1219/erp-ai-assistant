import json
from datetime import date

import pytest

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.integrations.feishu_sales import SalesBot
from tests.unit.test_feishu_app import event
from tests.unit.test_feishu_sales import MemoryStore, config


class Interpreter:
    def __init__(self, tool='sales_summary'):
        self.calls = []
        self.tool = tool

    def interpret(self, question, today):
        self.calls.append((question, today))
        return json.dumps({'action': 'query', 'unsupported_conditions': [], 'intent': {
            'tool': self.tool, 'start_date': '2026-09-01',
            'end_date_exclusive': '2026-09-03', 'metric': 'amount', 'top_n': 10}})


@pytest.fixture
def setup(extract, window, scope, source_as_of):
    report = analyze_sales(extract, window, scope, source_as_of=source_as_of,
                           rules=load_business_rules(), synthetic=True)
    return report, MemoryStore(), Interpreter()


@pytest.mark.parametrize('tool', ['sales_summary', 'sales_trend', 'product_ranking'])
def test_model_deferred_and_parameters_saved_before_reading_snapshot(setup, tool):
    report, store, model = setup
    model.tool = tool
    def load():
        assert 'question' not in str(store.rows[0]['payload'])
        assert store.rows[0]['payload']['request']['start_date'] == '2026-09-01'
        return report
    bot = SalesBot(config, 'ou_bot', store, load, model_client=model)
    topic = {'sales_summary': '整体销售表现', 'sales_trend': '每日销售趋势',
             'product_ranking': '商品销售额排行'}[tool]
    bot.accept(event(text=f'统计一下2026年9月1号到2号的{topic}'))
    assert model.calls == []
    cards = []
    assert bot.process_pending(lambda *args: pytest.fail('use card'),
                               card_sender=lambda mid, card, key: cards.append(card)) == 1
    assert len(model.calls) == 1 and isinstance(model.calls[0][1], date)
    assert '300.00' in json.dumps(cards, ensure_ascii=False)
    assert 'question' not in store.rows[0]['payload']
    assert bot.process_pending(lambda *args: None) == 0
    assert len(model.calls) == 1


def test_model_failure_scrubs_question_and_fixed_commands_still_work(setup):
    report, store, model = setup
    def fail(*args):
        raise TimeoutError('private model detail')
    model.interpret = fail
    bot = SalesBot(config, 'ou_bot', store, lambda: report, model_client=model)
    bot.accept(event(text='统计2026年9月1到2日的总体经营数据'))
    replies = []
    bot.process_pending(lambda mid, text, key: replies.append(text))
    assert 'question' not in store.rows[0]['payload']
    assert 'private' not in str(store.rows)
    store.rows.clear()
    bot.accept(event(text='2026-09-01至2026-09-02 销售概览'))
    bot.process_pending(lambda mid, text, key: replies.append(text))
    assert '300.00' in replies[-1]


def test_uncertain_model_call_after_restart_is_not_repeated(setup):
    report, store, model = setup
    bot = SalesBot(config, 'ou_bot', store, lambda: report, model_client=model)
    bot.accept(event(text='统计2026年9月1到2日的总体经营数据'))
    store.rows[0]['payload']['kind'] = 'interpreting'
    replies = []
    bot.process_pending(lambda mid, text, key: replies.append(text))
    assert not model.calls
    assert '重新提问' in replies[0]
    assert 'question' not in store.rows[0]['payload']


def test_revoked_or_unsupported_queries_never_call_model(setup):
    report, store, model = setup
    bot = SalesBot(config, 'ou_bot', store, lambda: report, model_client=model)
    assert bot.accept(event(user='intruder', text='查一下销售')) == 'ignored'
    bot.accept(event(text='9月1日药品销量排行'))
    bot.process_pending(lambda *args: None)
    assert not model.calls


def test_model_invalid_output_never_loads_report(setup):
    _, store, model = setup
    model.tool = 'execute_sql'
    bot = SalesBot(config, 'ou_bot', store, lambda: pytest.fail('invalid query'),
                   model_client=model)
    bot.accept(event(text='统计2026年9月1到2日的总体经营数据'))
    bot.process_pending(lambda *args: None)
    assert 'question' not in store.rows[0]['payload']
    assert store.rows[0]['result']['unavailable']


@pytest.mark.parametrize('question', [
    '2026-09-01至2026-09-02 上海客户销售概览', '销售概览',
    '2026-09-01至2026-09-02 商品销售额排行 前51',
    '2026-09-31至2026-10-01 商品销售额排行',
])
def test_model_cannot_drop_missing_dates_or_unsupported_constraints(setup, question):
    _, store, model = setup
    bot = SalesBot(config, 'ou_bot', store, lambda: pytest.fail('broadened query'),
                   model_client=model)
    bot.accept(event(text=question))
    bot.process_pending(lambda *args: None)
    assert not model.calls
    assert 'failed' not in store.rows[0]['result']
