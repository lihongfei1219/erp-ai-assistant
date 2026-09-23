"""Numbers bind to the latest delivered question in the isolated user history."""

from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.feishu_jobs import numbered_choice_context

NOW = datetime(2026, 9, 22, 8, tzinfo=timezone.utc)


def row(round_id="round-a", *, kind="semantic_notice", status="sent", age=1):
    return {
        "status": status,
        "created_at": NOW - timedelta(minutes=age),
        "updated_at": NOW - timedelta(minutes=age),
        "payload": {
            "kind": kind,
            "semantic_context": {"dialogue_id": round_id},
            "dialogue_turn": {
                "status": "needs_input", "choices": [{"id": "choice-a"}],
            },
        },
        "result": {"semantic_status": "needs_input"},
    }


def resolve(rows):
    return numbered_choice_context(rows, NOW)


def test_only_one_delivered_pending_round_can_accept_number():
    pending = row()
    assert resolve([pending]) == pending["payload"]
    assert resolve([pending, row(kind="clear_context", age=2), row("old", age=3)]) == (
        pending["payload"]
    )


@pytest.mark.parametrize("status", ["unknown", "rejected", "sending", "ready", "revoked"])
def test_unsent_or_uncertain_latest_round_never_accepts_number(status):
    assert resolve([row(status=status)]) is None


def test_newest_delivered_round_supersedes_old_unanswered_cards():
    latest = row("round-b")
    assert resolve([latest, row("round-a", age=2)]) == latest["payload"]


def test_intervening_new_question_or_clear_blocks_old_number():
    assert resolve([row(kind="analysis_planning"), row(age=2)]) is None
    assert resolve([row(kind="clear_context"), row(age=2)]) is None


def test_expired_or_delivered_after_arrival_round_cannot_accept_number():
    assert resolve([row(age=31)]) is None
    assert resolve([row(age=-1)]) is None


def test_repeated_display_of_same_round_does_not_create_a_new_mapping():
    pending = row()
    assert resolve([pending, row(age=2)]) == pending["payload"]


def test_answered_rounds_do_not_block_the_next_single_pending_choice():
    first, second, third = row("first", age=3), row("second", age=2), row("third")
    second["payload"]["answered_dialogue_id"] = "first"
    third["payload"]["answered_dialogue_id"] = "second"
    assert resolve([third, second, first]) == third["payload"]
    assert resolve([third, second, first, row("unanswered", age=4)]) == third["payload"]


@pytest.mark.parametrize("status", ["unknown", "rejected", "sending", "ready", "revoked"])
def test_older_uncertain_round_does_not_block_a_newer_delivered_question(status):
    first, second, latest = row("first", age=3), row("second", status=status, age=2), row("third")
    second["payload"]["answered_dialogue_id"] = "first"
    assert resolve([latest, second, first]) == latest["payload"]


def test_missing_dialogue_id_is_not_selectable():
    latest = row()
    latest["payload"]["semantic_context"] = {}
    assert resolve([latest]) is None
