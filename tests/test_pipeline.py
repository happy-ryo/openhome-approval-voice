"""Bridge + poller + ability data-path tests (design.md §1.2, §3.2).

M3.1: the pure logic no longer does file I/O or JSON itself (the ability does
that via the storage API). These tests therefore simulate the storage hop with
stdlib json — tests are not part of the deployed bundle, so they may use json.
"""

import json
from pathlib import Path

from approval_voice.ability import ApprovalVoiceAbility
from approval_voice.bridge import (
    items_from_raw,
    items_to_payload,
    notification_to_item,
    notifications_to_payload,
)
from approval_voice.poller import ReadCursor, seen_from_raw, seen_to_payload
from approval_voice.schema import GATES, AnnounceItem


def _items():
    return [
        AnnounceItem(id="a", gate="worker_complete", title="t", question="q",
                     subject="s", options=["承認", "差し戻し"]),
        AnnounceItem(id="b", gate="ci_merge", title="t", question="q",
                     subject="s", options=["マージ", "保留"]),
    ]


def test_example_queue_loads_and_covers_all_gates():
    queue = Path(__file__).parent.parent / "examples" / "announce_queue.json"
    data = json.loads(queue.read_text(encoding="utf-8"))
    items = items_from_raw(data)
    assert {i.gate for i in items} == set(GATES)


def test_bridge_is_public_hygiene_filter():
    # Fields outside the §1.3 whitelist must not leak into the item.
    notification = {
        "id": 7,
        "gate": "escalation",
        "title": "t",
        "question": "q",
        "subject": "s",
        "options": ["A", "B"],
        "created_at": "2026-05-31T10:00:00Z",
        "internal_state_path": "C:/secret/state.json",  # must be dropped
        "hook_name": "PreToolUse",                        # must be dropped
    }
    item = notification_to_item(notification)
    dumped = item.to_dict()
    assert "internal_state_path" not in dumped
    assert "hook_name" not in dumped
    assert item.id == "7"  # coerced to stable string


def test_queue_payload_roundtrips_through_storage_encoding():
    # The ability encodes the payload (json.dumps -> write_file); the daemon reads
    # it back (read_file -> json.loads -> items_from_raw). The pure logic only sees
    # already-decoded dicts; simulate the storage encode/decode hop with stdlib.
    notifications = [
        {"id": "a", "gate": "worker_complete", "subject": "s", "options": ["承認"]},
    ]
    payload = notifications_to_payload(notifications)          # bridge: notif -> dicts
    stored = json.dumps(payload, ensure_ascii=False)          # ability-side encode
    reread = items_from_raw(json.loads(stored))               # daemon-side decode
    assert [i.id for i in reread] == ["a"]


def test_items_to_payload_is_inverse_of_items_from_raw():
    items = _items()
    assert [i.id for i in items_from_raw(items_to_payload(items))] == ["a", "b"]


def test_cursor_dedups_within_batch():
    dup = AnnounceItem(id="a", gate="ci_merge", title="t", question="q", options=[])
    cursor = ReadCursor()
    assert len(cursor.unread([dup, dup])) == 1


def test_ability_speaks_once_then_dedups():
    spoken_log = []
    ability = ApprovalVoiceAbility(speak_fn=lambda s: spoken_log.append(s) or s)
    items = _items()

    first = ability.poll_once(items)
    assert len(first) == 2          # both spoken on first tick
    second = ability.poll_once(items)
    assert second == []             # read-cursor suppresses re-readout
    assert len(spoken_log) == 2     # never double-spoke


def test_unknown_gate_rejected():
    import pytest

    with pytest.raises(ValueError):
        AnnounceItem(id="x", gate="not_a_gate", title="t", question="q")


def test_seen_cursor_payload_roundtrips_across_restart():
    # M3: the on-device daemon restarts; a persisted cursor (seen_to_payload ->
    # json -> write_file, restored read_file -> json -> seen_from_raw) must
    # suppress already-spoken gates so they are never re-read aloud.
    cursor = ReadCursor()
    cursor.mark_read([AnnounceItem(id="a", gate="ci_merge", title="t", question="q")])
    stored = json.dumps(seen_to_payload(cursor))   # ability-side persist
    restored = seen_from_raw(json.loads(stored))   # ability-side load on restart
    assert restored == {"a"}


def test_smoke_sample_seeds_all_four_gates():
    # The daemon self-seeds approval_voice.sample.SAMPLE_NOTIFICATIONS on startup
    # (background_daemon has no trigger). It must cover all 4 gates and each must
    # render to a one-way readout, so the on-device smoke speaks the full set.
    from approval_voice.renderer import ONE_WAY_SUFFIX, render_speech
    from approval_voice.sample import SAMPLE_NOTIFICATIONS

    items = items_from_raw(notifications_to_payload(SAMPLE_NOTIFICATIONS))
    assert len(items) == 4
    assert {i.gate for i in items} == set(GATES)
    for i in items:
        assert render_speech(i).endswith(ONE_WAY_SUFFIX)


def test_seen_from_raw_is_defensive():
    # A corrupt cursor payload must yield "nothing seen", never crash the daemon
    # (the json-decode failure itself is caught in background.py).
    assert seen_from_raw([]) == set()
    assert seen_from_raw(None) == set()           # non-iterable -> empty
    assert seen_from_raw(["a", 2]) == {"a", "2"}  # ids coerced to stable strings
