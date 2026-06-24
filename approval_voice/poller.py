"""Read-cursor + dedup for the announce queue (design.md §3.2, §5-3).

Shared polling-runtime logic: track which queue items have already been read
aloud (by stable `id`) so the Background Ability never double-speaks or drops an
item. Read-cursor state is kept *locally* on the OpenHome side and is never
written back to the org — reading aloud has zero side effect on org state
(design.md §3.2).

M3.1 sandbox compliance (design.md §M3.1): module-scope data-encoding imports,
low-level platform access and raw file access are rejected by the add-capability
scan. So the poller stays **pure logic** with no file I/O and no data-encoding:
the read-cursor's persistence (read/write) lives in the ability layer
(`background.py`) via `capability_worker.read_file/write_file`, and the poller
only converts a decoded list <-> internal state (`seen_from_raw` /
`seen_to_payload`).
"""

from __future__ import annotations

from collections.abc import Iterable

from .schema import AnnounceItem


class ReadCursor:
    """Tracks spoken item ids; yields only fresh items, in order."""

    def __init__(self, seen: Iterable[str] | None = None) -> None:
        self._seen: set[str] = set(seen or ())

    def unread(self, items: list[AnnounceItem]) -> list[AnnounceItem]:
        """Return items not yet marked read, preserving queue order.

        Dedups within the same batch too (a queue listing the same id twice is
        spoken once).
        """
        fresh: list[AnnounceItem] = []
        batch_ids: set[str] = set()
        for item in items:
            if item.id in self._seen or item.id in batch_ids:
                continue
            batch_ids.add(item.id)
            fresh.append(item)
        return fresh

    def mark_read(self, items: Iterable[AnnounceItem]) -> None:
        for item in items:
            self._seen.add(item.id)

    @property
    def seen(self) -> set[str]:
        return set(self._seen)


def seen_from_raw(data) -> set[str]:
    """Build the seen-id set from an already-decoded cursor payload.

    The ability reads the cursor string via `capability_worker.read_file(...)`
    and decodes it with a method-local JSON parse; this pure function turns the
    decoded value into a set of string ids. Defensive on purpose: a malformed /
    non-iterable payload yields an empty set so a corrupt cursor never crashes
    the daemon (mirrors the old `load_seen` "corrupt -> nothing seen" guarantee;
    the decode failure itself is caught in `background.py`).
    """
    try:
        return {str(x) for x in data}
    except TypeError:
        return set()


def seen_to_payload(cursor: ReadCursor) -> list[str]:
    """Serialize the read-cursor into an encode-ready, stable-ordered id list.

    The ability turns this into a string with a method-local JSON dump and
    persists it via `capability_worker.write_file(...)`. Replaces the
    file-writing half of the old `save_seen`.
    """
    return sorted(cursor.seen)
