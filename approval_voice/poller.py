"""Read-cursor + dedup for the announce queue (design.md §3.2, §5-3).

Shared polling-runtime logic: track which queue items have already been read
aloud (by stable `id`) so the Background Ability never double-speaks or drops an
item. Read-cursor state is kept *locally* on the OpenHome side and is never
written back to the org — reading aloud has zero side effect on org state
(design.md §3.2).

This is pure in-memory logic in M2; the real Ability persists `seen` via
OpenHome `read_file()`/`write_file()` (design.md §4).
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
