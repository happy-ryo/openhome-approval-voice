"""Bridge + poller + ability data-path tests (design.md §1.2, §3.2)."""

from pathlib import Path

from approval_voice.ability import ApprovalVoiceAbility
from approval_voice.bridge import export_queue, load_queue, notification_to_item
from approval_voice.poller import ReadCursor, load_seen, save_seen
from approval_voice.schema import GATES, AnnounceItem


def _items():
    return [
        AnnounceItem(id="a", gate="worker_complete", title="t", question="q",
                     subject="s", options=["承認", "差し戻し"]),
        AnnounceItem(id="b", gate="ci_merge", title="t", question="q",
                     subject="s", options=["マージ", "保留"]),
    ]


def test_example_queue_loads_and_covers_all_gates(tmp_path=None):
    queue = Path(__file__).parent.parent / "examples" / "announce_queue.json"
    items = load_queue(queue)
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


def test_bridge_roundtrip(tmp_path):
    notifications = [
        {"id": "a", "gate": "worker_complete", "subject": "s", "options": ["承認"]},
    ]
    out = tmp_path / "queue.json"
    written = export_queue(notifications, out)
    reread = load_queue(out)
    assert [i.id for i in written] == [i.id for i in reread] == ["a"]


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


def test_seen_cursor_persists_across_restart(tmp_path):
    # M3: the on-device daemon restarts; a persisted cursor must suppress
    # already-spoken gates so they are never re-read aloud.
    seen_file = tmp_path / "nested" / "announce_seen.json"  # parent auto-created
    assert load_seen(seen_file) == set()                    # missing -> empty

    cursor = ReadCursor()
    cursor.mark_read([AnnounceItem(id="a", gate="ci_merge", title="t", question="q")])
    save_seen(seen_file, cursor)

    assert load_seen(seen_file) == {"a"}


def test_seen_cursor_corrupt_file_is_empty(tmp_path):
    bad = tmp_path / "announce_seen.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_seen(bad) == set()  # corrupt cursor must not crash the daemon


def test_export_queue_is_atomic_and_roundtrips(tmp_path):
    # Atomic write leaves no stray .tmp and yields a fully-parseable queue.
    out = tmp_path / "announce_queue.json"
    export_queue([{"id": "z", "gate": "escalation", "question": "q"}], out)
    assert out.exists()
    assert not (tmp_path / "announce_queue.json.tmp").exists()
    assert [i.id for i in load_queue(out)] == ["z"]
