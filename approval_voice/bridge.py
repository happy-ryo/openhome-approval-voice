"""Read-only state -> announce-queue bridge (design.md §1.2 step ②, §5).

Task deliverable (1): receive an `awaiting_user` notification (gate kind +
options) and emit a §1.3 AnnounceItem onto the shared queue.

This is the component shared with the sister project as the future
`openhome-org-voice-core` exporter base. It is strictly **read-only / one-way**
(design.md §3.2): it reads a pending-decision notification and appends to the
queue. It never writes back to the org and holds no return channel.

M2 note (§4): the org side is replaced by a *hand-written* notification dict.
The input shape here is deliberately conceptual — it does NOT mirror any real
claude-org state schema (public hygiene). M3 replaces this input with a real,
read-only export from live state.

Scope: this module holds only the pure mapping/hygiene filter
(`notification_to_item`). The actual queue file write lives in
`approval_voice.fileio` (PC side); keeping this module free of filesystem access
lets it ship inside the on-device ability bundle if needed.
"""

from __future__ import annotations

from . import codec
from .schema import AnnounceItem
from .transport import SAMPLE_NOTIFICATIONS


def notification_to_item(notification: dict) -> AnnounceItem:
    """Map a conceptual awaiting_user notification -> §1.3 AnnounceItem.

    The public-hygiene filter lives here (single choke point, §5): only the
    whitelisted conceptual fields cross into the queue. `options` absorbs the
    "no choices" case as an empty list rather than branching the schema.
    """
    return AnnounceItem(
        id=str(notification["id"]),
        gate=str(notification["gate"]),
        title=str(notification.get("title", "")),
        question=str(notification.get("question", "")),
        subject=str(notification.get("subject", "")),
        options=list(notification.get("options", [])),
        created_at=str(notification.get("created_at", "")),
    )


def sample_queue_json() -> str:
    """The canonical smoke sample as announce-queue text.

    Single seeding source for both the interactive entry (main.py) and the
    daemon's smoke bootstrap (background.py), built from the shared
    SAMPLE_NOTIFICATIONS through the same hygiene filter + codec as a real queue.
    """
    items = [notification_to_item(n) for n in SAMPLE_NOTIFICATIONS]
    return codec.items_to_json_str(items)
