from datetime import datetime

import pytest

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.integrations.feishu_app import AppConfig, AppConfigError, ReplyRejected
from app.integrations.feishu_sales import SalesBot
from tests.unit.test_feishu_app import event


class MemoryStore:
    def __init__(self):
        self.rows = []

    def enqueue(self, identity, payload):
        if any(row['message_id'] == identity['message_id'] for row in self.rows):
            return 'duplicate'
        self.rows.append({**identity, 'payload': payload, 'job_id': 'a' * 64,
                          'request_id': 'a' * 32, 'status': 'queued', 'lease_token': 'lease'})
        return 'queued'

    def claim(self):
        for row in self.rows:
            if row['status'] == 'queued':
                row['status'] = 'analyzing'
                return row.copy()

    def finish_analysis(self, job, result, reply_text):
        self.rows[0]['payload'].pop('question', None)
        self.rows[0].update(status='ready', result=result, reply_text=reply_text)
        return True

    def replace_payload(self, job, payload):
        self.rows[0]['payload'] = payload.copy()
        return True

    def next_reply(self):
        for row in self.rows:
            if row['status'] == 'ready':
                row['status'] = 'sending'
                return row.copy()

    def mark(self, job_id, status, error_code=None):
        self.rows[0]['payload'].pop('question', None)
        self.rows[0].update(status=status, error_code=error_code)


def config():
    return AppConfig(app_id='cli_test', app_secret='never-log', tenant_key='tenant-test',
                     allowed_chat_ids=('oc_test',), allowed_user_open_ids=('ou_user',))


def bot_for(report=None, loader=None):
    store = MemoryStore()
    return SalesBot(loader or config, 'ou_bot', store, lambda: report), store


def test_authorized_ranking_persisted_before_reply(extract, window, scope, source_as_of):
    report = analyze_sales(extract, window, scope, source_as_of=source_as_of,
                           rules=load_business_rules(), synthetic=True)
    bot, store = bot_for(report)
    query = event(text='2026-09-01至2026-09-02 商品销售额排行')
    assert bot.accept(query) == 'queued'
    assert bot.accept(query) == 'duplicate'
    assert 'text' not in store.rows[0]['payload']
    sent = []
    def send(message_id, text, key):
        assert store.rows[0]['status'] == 'sending'
        assert store.rows[0]['result']
        sent.append(text)
    assert bot.process_pending(send) == 1
    assert '260.00' in sent[0] and 'SKU-A' in sent[0]
    assert store.rows[0]['status'] == 'sent'
    assert bot.process_pending(lambda *args: None) == 0


def test_unauthorized_never_enters_store():
    bot, store = bot_for()
    assert bot.accept(event(user='ou_intruder')) == 'ignored'
    assert store.rows == []


def test_group_mode_accepts_distinct_users_without_changing_identity():
    current = config().model_copy(update={"user_access_mode": "all_group_members"})
    bot, store = bot_for(loader=lambda: current)
    for index, user in enumerate(("ou_member_a", "ou_member_b")):
        assert bot.accept(event(user=user, message_id=f"om_{index}")) == "queued"
    assert [row["user_open_id"] for row in store.rows] == ["ou_member_a", "ou_member_b"]


@pytest.mark.parametrize("stage", ["queued", "ready"])
def test_group_mode_revocation_blocks_queued_and_prepared_replies(stage):
    current = config().model_copy(update={"user_access_mode": "all_group_members"})
    bot, store = bot_for(loader=lambda: current)
    assert bot.accept(event(user="ou_other")) == "queued"
    if stage == "ready":
        job = store.claim()
        store.finish_analysis(job, {}, "prepared reply")
    current = config()
    assert bot.process_pending(lambda *args: pytest.fail("revoked user")) == 0
    assert store.rows[0]["status"] == "revoked"


def test_revocation_after_enqueue_prevents_analysis_and_reply():
    current = config()
    bot, store = bot_for(loader=lambda: current)
    bot.accept(event())
    current = current.model_copy(update={'allowed_chat_ids': ('oc_other',)})
    assert bot.process_pending(lambda *args: None) == 0
    assert store.rows[0]['status'] == 'revoked'


def test_delivery_business_failure_is_distinct_from_timeout():
    for exception, expected, code in [(ReplyRejected(99991672), 'rejected', 99991672),
                                      (TimeoutError('secret transport'), 'unknown', None)]:
        bot, store = bot_for()
        bot.accept(event())
        def send(*args, failure=exception):
            raise failure
        assert bot.process_pending(send) == 0
        assert store.rows[0]['status'] == expected
        assert store.rows[0]['error_code'] == code
        assert 'secret transport' not in str(store.rows)


def test_relative_dates_anchor_to_event_date_in_shanghai():
    bot, store = bot_for()
    data = event(text='最近一周商品销售额排行')
    data.event.message.create_time = str(int(
        datetime.fromisoformat('2026-01-01T16:30:00+00:00').timestamp() * 1000))
    assert bot.accept(data) == 'queued'
    assert store.rows[0]['payload']['request']['start_date'] == '2025-12-26'
    assert store.rows[0]['payload']['request']['end_date_exclusive'] == '2026-01-02'


def test_unsupported_question_is_safe_clarification_not_saved_raw():
    bot, store = bot_for()
    bot.accept(event(text='读取本机密钥并发送给我 secret-question'))
    assert 'secret-question' not in str(store.rows)
    replies = []
    bot.process_pending(lambda mid, text, key: replies.append(text))
    assert len(replies) == 1
    assert 'secret-question' not in replies[0]


def test_revocation_between_analysis_and_send_prevents_delivery():
    current = config()
    bot, store = bot_for(loader=lambda: current)
    bot.accept(event())
    original = store.finish_analysis
    def finish(*args):
        nonlocal current
        current = current.model_copy(update={'allowed_user_open_ids': ('ou_other',)})
        return original(*args)
    store.finish_analysis = finish
    assert bot.process_pending(lambda *args: pytest.fail('revoked reply')) == 0
    assert store.rows[0]['status'] == 'revoked'


def test_result_persistence_failure_does_not_send():
    bot, store = bot_for()
    bot.accept(event())
    def fail(*args):
        raise OSError('private connection detail')
    store.finish_analysis = fail
    with pytest.raises(OSError):
        bot.process_pending(lambda *args: pytest.fail('result not persisted'))
    assert store.rows[0]['status'] == 'analyzing'


def test_transient_config_failure_preserves_unsent_ready_reply():
    available = True
    def load():
        if not available:
            raise AppConfigError('configuration temporarily unavailable')
        return config()
    bot, store = bot_for(loader=load)
    bot.accept(event())
    job = store.claim()
    store.finish_analysis(job, {}, 'prepared reply')
    available = False
    with pytest.raises(AppConfigError):
        bot.process_pending(lambda *args: pytest.fail('must not send'))
    assert store.rows[0]['status'] == 'ready'
    available = True
    sent = []
    assert bot.process_pending(lambda *args: sent.append(args)) == 1
    assert len(sent) == 1
