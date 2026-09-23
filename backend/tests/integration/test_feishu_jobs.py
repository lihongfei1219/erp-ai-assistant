"""Opt-in tests write synthetic tasks ONLY to ERP_AI_Assistant."""

import os
import uuid

import pytest

from app.integrations.feishu_jobs import SqlJobStore

pytestmark = pytest.mark.skipif(
    os.getenv("ERP_RUN_ASSISTANT_TESTS") != "1",
    reason="Requires isolated assistant SQL Server database",
)


@pytest.fixture
def store():
    value = SqlJobStore("cli_test_" + uuid.uuid4().hex, "tenant-synthetic")
    yield value
    with value.connection() as connection:
        connection.execute("DELETE FROM erp_ai.feishu_sales_jobs WHERE app_id = ?", value.app_id)
        connection.commit()


def identity(message="om_1", event="evt_1", user="ou_test"):
    return dict(chat_id="oc_synthetic", user_open_id=user, message_id=message, event_id=event)


def test_transactional_dedup_and_result_persistence(store):
    assert store.enqueue(identity(), {"kind": "help"}) == "queued"
    assert store.enqueue(identity(event="evt_again"), {"kind": "help"}) == "duplicate"
    assert store.enqueue(identity(message="om_other"), {"kind": "help"}) == "duplicate"
    job = store.claim()
    assert job["payload"] == {"kind": "help"}
    assert store.claim() is None
    assert store.finish_analysis(job, {"amount": "260.0000"}, "synthetic reply")
    reopened = SqlJobStore(store.app_id, store.tenant_key)
    outgoing = reopened.next_reply()
    assert outgoing["result"]["amount"] == "260.0000"
    assert outgoing["reply_text"] == "synthetic reply"
    reopened.mark(outgoing["job_id"], "sent")
    assert reopened.next_reply() is None


def test_restart_recovers_analysis_but_never_resends_uncertain_delivery(store):
    store.enqueue(identity(), {"kind": "help"})
    first = store.claim()
    with store.connection() as connection:
        connection.execute(
            "UPDATE erp_ai.feishu_sales_jobs SET lease_until = "
            "DATEADD(second, -1, SYSUTCDATETIME()) WHERE job_id = ?",
            first["job_id"],
        )
        connection.commit()
    second = store.claim()
    assert second["job_id"] == first["job_id"]
    assert second["lease_token"] != first["lease_token"]
    assert not store.finish_analysis(first, {}, "stale worker")
    assert store.finish_analysis(second, {}, "recovered")
    assert store.next_reply() is not None
    store.recover()
    assert store.next_reply() is None
    with store.connection() as connection:
        status = connection.execute(
            "SELECT status FROM erp_ai.feishu_sales_jobs WHERE job_id = ?", first["job_id"]
        ).fetchone()[0]
    assert status == "unknown"


def test_user_concurrency_and_application_isolation(store):
    store.enqueue(identity(), {"kind": "ranking"})
    assert store.enqueue(identity("om_2", "evt_2"), {"kind": "ranking"}) == "busy"
    other = SqlJobStore("cli_another_" + uuid.uuid4().hex, store.tenant_key)
    assert other.claim() is None
    assert other.next_reply() is None
    outgoing = store.next_reply()
    assert "正在处理" in outgoing["reply_text"]
    store.mark(outgoing["job_id"], "rejected", error_code=99991672)
    assert store.next_reply() is None


def test_interpretation_state_is_durable_and_lease_fenced(store):
    store.enqueue(identity(), {"kind": "interpret", "question": "synthetic question"})
    job = store.claim()
    stale = {**job, "lease_token": "expired-token"}
    assert not store.replace_payload(stale, {"kind": "notice"})
    assert store.replace_payload(job, {"kind": "interpreting"})
    payload = {"kind": "summary", "request": {"start_date": "2026-09-01"}}
    assert store.replace_payload(job, payload)
    assert store.finish_analysis(job, {"total_amount": "300.00"}, "synthetic")
    reopened = SqlJobStore(store.app_id, store.tenant_key)
    outgoing = reopened.next_reply()
    assert outgoing["payload"] == payload
    reopened.mark(outgoing["job_id"], "sent")


@pytest.mark.parametrize("revoked", [True, False])
def test_raw_question_removed_on_completion_or_revocation(store, revoked):
    store.enqueue(identity(), {"kind": "interpret", "question": "synthetic question"})
    job = store.claim()
    if revoked:
        store.mark(job["job_id"], "revoked")
    else:
        assert store.finish_analysis(job, {}, "safe failure")
    with store.connection() as connection:
        raw = connection.execute(
            "SELECT payload_json FROM erp_ai.feishu_sales_jobs WHERE job_id=?", job["job_id"]
        ).fetchone()[0]
    assert "question" not in raw


def delivered_analysis(store):
    payload = {"kind": "analysis", "plan": {"steps": []}, "snapshot_key": "synthetic"}
    store.enqueue(identity(), payload)
    job = store.claim()
    assert store.finish_analysis(job, {"run_id": "synthetic-result"}, "synthetic")
    outgoing = store.next_reply()
    store.mark(outgoing["job_id"], "sent")
    store.enqueue(identity("om_follow", "evt_follow"), {"kind": "analysis_question"})
    return job, store.claim(), payload


def test_analysis_context_survives_restart_and_isolates_all_identity_dimensions(store):
    _, current, payload = delivered_analysis(store)
    reopened = SqlJobStore(store.app_id, store.tenant_key)
    assert reopened.latest_analysis_context(current) == payload
    assert reopened.latest_analysis_context(dict(current, chat_id="another-chat")) is None
    assert reopened.latest_analysis_context(dict(current, user_open_id="another-user")) is None
    assert SqlJobStore("another-app", store.tenant_key).latest_analysis_context(current) is None
    assert SqlJobStore(store.app_id, "another-tenant").latest_analysis_context(current) is None


@pytest.mark.parametrize("condition", ["expired", "unknown", "failed_result", "delivered_later"])
def test_ineligible_analysis_never_becomes_followup_context(store, condition):
    previous, current, _ = delivered_analysis(store)
    with store.connection() as connection:
        updates = {
            "expired": "updated_at=DATEADD(minute,-31,SYSUTCDATETIME())",
            "unknown": "status='unknown'",
            "failed_result": "result_json=N'{\"unavailable\":true}'",
            "delivered_later": "updated_at=DATEADD(minute,1,SYSUTCDATETIME())",
        }
        connection.execute(
            "UPDATE erp_ai.feishu_sales_jobs SET " + updates[condition] + " WHERE job_id=?",
            previous["job_id"],
        )
        connection.commit()
    assert store.latest_analysis_context(current) is None


def test_clear_context_prevents_falling_back_to_older_analysis(store):
    _, current, _ = delivered_analysis(store)
    assert store.replace_payload(current, {"kind": "clear_context"})
    assert store.finish_analysis(current, {}, "cleared")
    outgoing = store.next_reply()
    store.mark(outgoing["job_id"], "sent")
    store.enqueue(identity("om_after_clear", "evt_after_clear"), {"kind": "analysis_question"})
    assert store.latest_analysis_context(store.claim()) is None


def test_context_expiry_is_anchored_to_question_arrival_not_processing_time(store):
    previous, current, payload = delivered_analysis(store)
    with store.connection() as connection:
        connection.execute(
            "UPDATE erp_ai.feishu_sales_jobs SET created_at=DATEADD(minute,-31,SYSUTCDATETIME()), "
            "updated_at=DATEADD(minute,-31,SYSUTCDATETIME()) WHERE job_id=?",
            previous["job_id"],
        )
        current["created_at"] = connection.execute(
            "SELECT DATEADD(minute,-2,SYSUTCDATETIME())",
        ).fetchone()[0]
        connection.commit()
    assert store.latest_analysis_context(current) == payload


def test_clear_is_a_barrier_even_when_its_delivery_finishes_after_next_question_arrives(store):
    _, clear, _ = delivered_analysis(store)
    assert store.replace_payload(clear, {"kind": "clear_context"})
    assert store.finish_analysis(clear, {}, "cleared")
    store.enqueue(identity("om_after_clear", "evt_after_clear"), {"kind": "analysis_question"})
    current = store.claim()
    outgoing = store.next_reply()
    store.mark(outgoing["job_id"], "sent")
    with store.connection() as connection:
        connection.execute(
            "UPDATE erp_ai.feishu_sales_jobs SET updated_at=DATEADD(second,5,SYSUTCDATETIME()) "
            "WHERE job_id=?",
            clear["job_id"],
        )
        connection.commit()
    assert store.latest_analysis_context(current) is None


def test_delivered_semantic_clarification_is_durable_context(store):
    payload = {
        "kind": "semantic_notice",
        "snapshot_key": "synthetic",
        "status": "clarify",
        "notice": "请补充统计日期",
        "semantic_context": {"intents": [{"domain": "sales"}], "pending": ["time"]},
    }
    store.enqueue(identity(), payload)
    previous = store.claim()
    assert store.finish_analysis(
        previous, {"semantic_status": "clarify"}, "synthetic clarification"
    )
    outgoing = store.next_reply()
    store.mark(outgoing["job_id"], "sent")
    store.enqueue(
        identity("om_semantic_follow", "evt_semantic_follow"), {"kind": "analysis_question"}
    )
    current = store.claim()
    assert SqlJobStore(store.app_id, store.tenant_key).latest_analysis_context(current) == payload
    with store.connection() as connection:
        connection.execute(
            "UPDATE erp_ai.feishu_sales_jobs SET status='unknown' WHERE job_id=?",
            previous["job_id"],
        )
        connection.commit()
    assert store.latest_analysis_context(current) is None


def deliver_guidance(store, sequence, *, round_id, status="sent"):
    payload = {
        "kind": "semantic_notice", "status": "needs_input",
        "semantic_context": {"dialogue_id": round_id},
        "dialogue_turn": {"choices": [{"id": "synthetic-choice"}]},
    }
    store.enqueue(identity(f"om_guide_{sequence}", f"evt_guide_{sequence}"), payload)
    job = store.claim()
    assert store.finish_analysis(job, {"semantic_status": "needs_input"}, "synthetic guide")
    outgoing = store.next_reply()
    store.mark(outgoing["job_id"], status)
    return payload


def numbered_reply(store, sequence="next"):
    store.enqueue(
        identity(f"om_number_{sequence}", f"evt_number_{sequence}"),
        {"kind": "analysis_question", "question": "1"},
    )
    return store.claim()


def test_numbered_context_survives_restart_and_is_identity_scoped(store):
    payload = deliver_guidance(store, 1, round_id="round-1")
    current = numbered_reply(store)
    reopened = SqlJobStore(store.app_id, store.tenant_key)
    assert reopened.latest_numbered_choice_context(current) == payload
    assert reopened.latest_numbered_choice_context(dict(current, chat_id="another")) is None
    assert reopened.latest_numbered_choice_context(dict(current, user_open_id="another")) is None
    assert SqlJobStore("another", store.tenant_key).latest_numbered_choice_context(current) is None
    assert SqlJobStore(store.app_id, "another").latest_numbered_choice_context(current) is None


@pytest.mark.parametrize("intervening", ["failed_question", "unknown", "clear"])
def test_numbered_context_blocks_latest_unavailable_task_or_clear(store, intervening):
    deliver_guidance(store, 1, round_id="round-1")
    if intervening == "unknown":
        deliver_guidance(
            store, 2, round_id="round-2", status="unknown" if intervening == "unknown" else "sent",
        )
    else:
        job = numbered_reply(store, "intervening")
        kind = "clear_context" if intervening == "clear" else "analysis_planning"
        assert store.replace_payload(job, {"kind": kind})
        assert store.finish_analysis(job, {}, "synthetic")
        outgoing = store.next_reply()
        store.mark(outgoing["job_id"], "sent")
    assert store.latest_numbered_choice_context(numbered_reply(store)) is None


def test_latest_delivered_guidance_supersedes_older_round_in_database(store):
    deliver_guidance(store, 1, round_id="round-1")
    latest = deliver_guidance(store, 2, round_id="round-2")
    assert store.latest_numbered_choice_context(numbered_reply(store)) == latest
