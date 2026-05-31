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
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .schema import AnnounceItem


def _atomic_write_text(path: str | Path, text: str) -> None:
    """Write via temp file + os.replace so the on-device poller (background.py)
    never reads a half-written queue (M3 transport reliability, design.md §M3)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


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


def load_queue(queue_path: str | Path) -> list[AnnounceItem]:
    """Read the shared announce queue (JSON array) into AnnounceItems."""
    raw = Path(queue_path).read_text(encoding="utf-8")
    data = json.loads(raw)
    return [AnnounceItem.from_dict(entry) for entry in data]


def export_queue(notifications: list[dict], queue_path: str | Path) -> list[AnnounceItem]:
    """Map notifications -> items and write the shared queue (append-only intent).

    Writes the full array for the M2 mock; the real bridge appends. Returns the
    items written so callers can assert on them without re-reading the file.
    """
    items = [notification_to_item(n) for n in notifications]
    payload = [item.to_dict() for item in items]
    _atomic_write_text(
        queue_path, json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return items
