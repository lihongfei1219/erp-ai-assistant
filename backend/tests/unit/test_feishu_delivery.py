import base64
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event

import pytest

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.notifications.daily import build_digest
from app.notifications.feishu import (
    DeliveryError,
    FeishuSettings,
    delivery_lock,
    send_once,
    send_webhook,
)


@pytest.fixture
def digest(extract, window, scope, source_as_of):
    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    return build_digest(report, date(2026, 9, 2), demo=True)


@pytest.fixture
def settings(tmp_path):
    return FeishuSettings(
        webhook="https://open.feishu.cn/open-apis/bot/v2/hook/test-hook",
        secret="test-secret",
        state_dir=tmp_path,
    )


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return json.dumps(self.payload).encode()


def test_webhook_payload_signature_and_success(settings, digest):
    captured = []

    def transport(request, timeout):
        captured.append(json.loads(request.data))
        assert timeout > 0
        return Response({"code": 0, "msg": "success", "data": {}})

    send_webhook(settings, digest, opener=transport, timestamp=1700000000)
    payload = captured[0]
    expected = base64.b64encode(
        hmac.new(b"1700000000\ntest-secret", b"", hashlib.sha256).digest()
    ).decode()
    assert payload["sign"] == expected
    assert payload["timestamp"] == "1700000000"
    assert payload["msg_type"] == "interactive"
    assert "2026-09-02" in payload["card"]["header"]["title"]["content"]
    assert "200.00" in json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("payload", [{"code": 19024}, {}, {"code": False}, {"code": "0"}])
def test_non_success_response_never_marks_delivered(settings, digest, payload):
    with pytest.raises(DeliveryError):
        send_webhook(settings, digest, opener=lambda *a, **kw: Response(payload))


def test_success_survives_new_settings_and_stops_duplicate(settings, digest):
    sends = []
    assert send_once(settings, digest, sender=lambda *a: sends.append(1)).status == "sent"
    reopened = FeishuSettings(webhook=settings.webhook, state_dir=settings.state_dir)
    assert send_once(reopened, digest, sender=lambda *a: sends.append(1)).status == "already_sent"
    assert sends == [1]
    for path in settings.state_dir.glob("*.json"):
        assert settings.webhook not in path.read_text()
        assert settings.secret not in path.read_text()


def test_ambiguous_timeout_is_not_automatically_retried(settings, digest):
    def timeout(*args):
        raise DeliveryError("发送结果不确定", uncertain=True)

    with pytest.raises(DeliveryError):
        send_once(settings, digest, sender=timeout)
    with pytest.raises(DeliveryError, match="核实"):
        send_once(settings, digest, sender=lambda *a: pytest.fail("must not send"))


def test_definite_failure_can_be_retried(settings, digest):
    def rejected(*args):
        raise DeliveryError("飞书拒绝消息", uncertain=False)

    with pytest.raises(DeliveryError):
        send_once(settings, digest, sender=rejected)
    assert send_once(settings, digest, sender=lambda *a: None).status == "sent"


def test_os_lock_blocks_second_sender_and_releases_on_exit(tmp_path):
    entered = Event()
    release = Event()

    def holder():
        with delivery_lock(tmp_path / "same.lock"):
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(holder)
        assert entered.wait(5)
        try:
            with pytest.raises(DeliveryError):
                with delivery_lock(tmp_path / "same.lock"):
                    pytest.fail("lock must be exclusive")
        finally:
            release.set()
        future.result()
    with delivery_lock(tmp_path / "same.lock"):
        pass


@pytest.mark.parametrize(
    "url",
    [
        "http://open.feishu.cn/open-apis/bot/v2/hook/x",
        "https://evil.example/hook/x",
        "https://open.feishu.cn.evil.example/hook/x",
        "https://open.feishu.cn/open-apis/bot/v2/hook/x?secret=abc",
    ],
)
def test_wrong_destination_rejected_before_network(tmp_path, digest, url):
    config = FeishuSettings(webhook=url, state_dir=tmp_path)
    with pytest.raises(DeliveryError):
        send_once(config, digest, sender=lambda *a: pytest.fail("must not send"))
