import json
from types import SimpleNamespace

import pytest
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from app.integrations.feishu_app import (
    AppConfig,
    AppConfigError,
    ConnectionBot,
    ReplyRejected,
    authorized_identity,
    load_config,
    probe_bot,
    reply_text,
)


@pytest.fixture
def config():
    return AppConfig(app_id="cli_test", app_secret="secret-never-log",
                     tenant_key="tenant-test", allowed_chat_ids=("oc_test",),
                     allowed_user_open_ids=("ou_user",))


def event(*, user="ou_user", chat="oc_test", bot="ou_bot", app="cli_test",
          tenant="tenant-test", text="连接测试", message_id="om_test", event_id="evt_1"):
    return P2ImMessageReceiveV1({
        "schema": "2.0",
        "header": {"app_id": app, "tenant_key": tenant, "event_id": event_id,
                   "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": user},
                       "tenant_key": tenant},
            "message": {"message_id": message_id, "chat_id": chat,
                        "chat_type": "group", "message_type": "text",
                        "content": json.dumps({"text": "@_user_1 " + text}),
                        "mentions": [{"key": "@_user_1", "id": {"open_id": bot}}]},
        },
    })


def test_config_validates_without_exposing_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"app_id": "cli_test", "app_secret": "sensitive"}),
                    encoding="utf-8")
    settings = load_config(path)
    assert "sensitive" not in repr(settings)
    with pytest.raises(AppConfigError):
        settings.require_authorized_groups()
    path.write_text('{"app_secret": "sensitive", "unexpected": true}', encoding="utf-8")
    with pytest.raises(AppConfigError) as caught:
        load_config(path)
    assert "sensitive" not in str(caught.value)


def test_missing_config_explains_setup_without_exposing_partial_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-never-log")
    with pytest.raises(AppConfigError) as caught:
        load_config(tmp_path / "missing.json")
    assert "配置文件不存在" in str(caught.value)
    assert "--config" in str(caught.value)
    assert "secret-never-log" not in str(caught.value)


def test_environment_credentials_still_need_explicit_group_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-never-log")
    settings = load_config(tmp_path / "missing.json")
    settings.require_credentials()
    with pytest.raises(AppConfigError):
        settings.require_authorized_groups()


def test_group_mode_loads_without_user_allowlist(config, tmp_path, monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    data = config.model_dump(mode="json")
    data.update(user_access_mode="all_group_members", allowed_user_open_ids=[])
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    settings = load_config(path)
    settings.require_authorized_groups()
    data["user_access_mode"] = "all"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AppConfigError):
        load_config(path)
    data.pop("user_access_mode")
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AppConfigError):
        load_config(path).require_authorized_groups()


@pytest.mark.parametrize("changes", [{"tenant_key": ""}, {"allowed_chat_ids": ()}])
def test_group_mode_still_requires_explicit_scope(config, changes):
    settings = config.model_copy(update={"user_access_mode": "all_group_members", **changes})
    with pytest.raises(AppConfigError):
        settings.require_authorized_groups()


def test_group_mode_accepts_unlisted_user_and_can_be_revoked(config, tmp_path):
    settings = config.model_copy(update={"user_access_mode": "all_group_members"})
    bot = ConnectionBot(settings, "ou_bot", tmp_path)
    assert bot.accept(event(user="ou_other")) == "queued"
    reopened = ConnectionBot(config, "ou_bot", tmp_path)
    assert reopened.process_pending(lambda *a: pytest.fail("revoked user")) == 0


@pytest.mark.parametrize("changes", [
    {"chat": "oc_other"}, {"app": "cli_other"}, {"tenant": "other"},
    {"bot": "ou_other_bot"}, {"user": "ou_bot"},
])
def test_group_mode_does_not_bypass_event_boundaries(config, tmp_path, changes):
    settings = config.model_copy(update={"user_access_mode": "all_group_members"})
    bot = ConnectionBot(settings, "ou_bot", tmp_path)
    assert bot.accept(event(**changes)) == "ignored"


@pytest.mark.parametrize("user", [None, "", "ou_", "invalid"])
def test_group_mode_requires_valid_user_identity(config, user):
    settings = config.model_copy(update={"user_access_mode": "all_group_members"})
    assert not authorized_identity(settings, {
        "app_id": config.app_id, "tenant_key": config.tenant_key,
        "chat_id": config.allowed_chat_ids[0], "user_open_id": user,
    })


def test_discovery_records_ids_without_reply_or_message_content(config, tmp_path):
    bot = ConnectionBot(config, "ou_bot", tmp_path, discover=True)
    assert bot.accept(event(text="this is private content")) == "discovered"
    stored = (tmp_path / "discovery.json").read_text(encoding="utf-8")
    assert "oc_test" in stored and "ou_user" in stored and "tenant-test" in stored
    assert "private content" not in stored
    assert "secret-never-log" not in stored
    assert bot.process_pending(lambda *a: pytest.fail("discovery never sends")) == 0


@pytest.mark.parametrize("changes", [
    {"user": "ou_other"}, {"chat": "oc_other"}, {"app": "cli_other"},
    {"tenant": "other"}, {"bot": "ou_other_bot"},
])
def test_unauthorized_or_wrong_bot_events_are_ignored(config, tmp_path, changes):
    bot = ConnectionBot(config, "ou_bot", tmp_path)
    assert bot.accept(event(**changes)) == "ignored"
    assert bot.process_pending(lambda *a: pytest.fail("must not send")) == 0


def test_duplicate_message_is_persisted_once_and_survives_restart(config, tmp_path):
    bot = ConnectionBot(config, "ou_bot", tmp_path)
    assert bot.accept(event()) == "queued"
    assert bot.accept(event(event_id="another-delivery")) == "duplicate"
    sent = []
    reopened = ConnectionBot(config, "ou_bot", tmp_path)
    assert reopened.process_pending(lambda message_id, text, key: sent.append(
        (message_id, text, key))) == 1
    assert sent[0][0] == "om_test"
    assert "连接正常" in sent[0][1]
    assert "secret-never-log" not in sent[0][1]
    assert reopened.process_pending(lambda *a: pytest.fail("must not resend")) == 0


def test_permissions_rechecked_before_deferred_reply(config, tmp_path):
    bot = ConnectionBot(config, "ou_bot", tmp_path)
    bot.accept(event())
    revoked = config.model_copy(update={"allowed_user_open_ids": ("ou_replacement",)})
    reopened = ConnectionBot(revoked, "ou_bot", tmp_path)
    assert reopened.process_pending(lambda *a: pytest.fail("revoked user")) == 0


def test_reply_timeout_never_causes_blind_retry(config, tmp_path):
    bot = ConnectionBot(config, "ou_bot", tmp_path)
    bot.accept(event())
    def timeout(*args):
        raise TimeoutError("sensitive transport detail")
    assert bot.process_pending(timeout) == 0
    reopened = ConnectionBot(config, "ou_bot", tmp_path)
    assert reopened.process_pending(lambda *a: pytest.fail("ambiguous reply")) == 0
    records = list((tmp_path / "inbox").glob("*.json"))
    assert json.loads(records[0].read_bytes())["status"] == "unknown"
    assert "sensitive transport detail" not in records[0].read_text()


def test_probe_uses_bot_info_and_checks_business_code():
    requests = []
    def request(req):
        requests.append(req)
        return SimpleNamespace(code=0, raw=SimpleNamespace(
            content=b'{"code":0,"bot":{"open_id":"ou_bot","activate_status":2}}'))
    assert probe_bot(SimpleNamespace(request=request)) == "ou_bot"
    assert requests[0].uri == "/open-apis/bot/v3/info/"
    with pytest.raises(AppConfigError):
        probe_bot(SimpleNamespace(request=lambda req: SimpleNamespace(code=999, msg="secret")))


def test_reply_targets_original_message_and_uses_stable_uuid():
    requests = []
    def reply(req):
        requests.append(req)
        return SimpleNamespace(code=0, data=SimpleNamespace(message_id="om_reply"))
    client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(
        message=SimpleNamespace(reply=reply))))
    reply_text(client, "om_source", "连接正常", "stable-key")
    assert requests[0].message_id == "om_source"
    assert requests[0].request_body.uuid == "stable-key"
    assert json.loads(requests[0].request_body.content)["text"] == "连接正常"


def test_reply_rejection_retains_only_numeric_error_code():
    client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(
        reply=lambda req: SimpleNamespace(code=99991672, msg='secret response')))))
    with pytest.raises(ReplyRejected) as caught:
        reply_text(client, 'om_source', 'test', 'key')
    assert caught.value.code == 99991672
    assert 'secret response' not in str(caught.value)
