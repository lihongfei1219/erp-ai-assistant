from datetime import datetime

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.core.reports import save_report
from app.notifications.feishu import DeliveryError, FeishuSettings
from workers.feishu_daily import scheduled_tick


def test_schedule_uses_shanghai_yesterday_and_runs_once_after_eight(
    tmp_path,
    extract,
    window,
    scope,
    source_as_of,
):
    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    path = tmp_path / "report.json"
    save_report(report, path)
    settings = FeishuSettings(
        webhook="https://open.feishu.cn/open-apis/bot/v2/hook/test", state_dir=tmp_path / "state"
    )
    sent = []

    def sender(config, digest):
        sent.append(digest)

    assert (
        scheduled_tick(
            path, settings, datetime.fromisoformat("2026-09-02T23:59:00+00:00"), sender=sender
        )
        == "waiting"
    )
    assert not sent
    assert (
        scheduled_tick(
            path, settings, datetime.fromisoformat("2026-09-03T00:00:00+00:00"), sender=sender
        )
        == "sent"
    )
    assert str(sent[0].day) == "2026-09-02"
    assert sent[0].demo is False
    assert (
        scheduled_tick(
            path, settings, datetime.fromisoformat("2026-09-03T03:00:00+00:00"), sender=sender
        )
        == "already_sent"
    )
    assert len(sent) == 1


def test_stale_snapshot_is_not_sent_and_can_recover_after_refresh(
    tmp_path,
    extract,
    window,
    scope,
    source_as_of,
):
    settings = FeishuSettings(
        webhook="https://open.feishu.cn/open-apis/bot/v2/hook/test", state_dir=tmp_path / "state"
    )
    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    path = tmp_path / "report.json"
    save_report(report, path)
    now = datetime.fromisoformat("2026-09-20T08:00:00+08:00")
    sends = []
    assert scheduled_tick(path, settings, now, sender=lambda *a: sends.append(1)) == "failed"
    assert not sends
    assert scheduled_tick(path, settings, now, sender=lambda *a: sends.append(1)) == "waiting"


def test_delivery_uncertainty_stops_schedule_retry(tmp_path, extract, window, scope, source_as_of):
    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    path = tmp_path / "report.json"
    save_report(report, path)
    settings = FeishuSettings(
        webhook="https://open.feishu.cn/open-apis/bot/v2/hook/test", state_dir=tmp_path / "state"
    )

    def uncertain(*args):
        raise DeliveryError("unknown", uncertain=True)

    assert (
        scheduled_tick(
            path, settings, datetime.fromisoformat("2026-09-03T08:00:00+08:00"), sender=uncertain
        )
        == "unknown"
    )
    assert (
        scheduled_tick(
            path,
            settings,
            datetime.fromisoformat("2026-09-03T08:10:00+08:00"),
            sender=lambda *a: (_ for _ in ()).throw(AssertionError()),
        )
        == "unknown"
    )
