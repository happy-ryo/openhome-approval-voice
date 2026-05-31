"""Filesystem I/O for the announce queue + read-cursor (PC tools + tests).

This module is the **PC-side / test-side** file layer. It is intentionally NOT
shipped in the on-device capability bundle (`deploy/build_zip.py` excludes it):
the OpenHome capability sandbox forbids the filesystem module and raw file-open,
so on-device the daemon reads/writes through `capability_worker` instead (see
`openhome_ability/background.py`). Both layers serialize via `approval_voice.codec`
so the wire format keeps a single source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import codec
from .bridge import notification_to_item
from .schema import AnnounceItem


def _atomic_write_text(path: str | Path, text: str) -> None:
    """Write via temp file + os.replace so a concurrent reader never observes a
    half-written file (queue/cursor transport reliability, design.md §M3)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def load_queue(queue_path: str | Path) -> list[AnnounceItem]:
    """Read the shared announce queue (JSON array) into AnnounceItems."""
    return codec.items_from_json_str(Path(queue_path).read_text(encoding="utf-8"))


def export_queue(
    notifications: list[dict], queue_path: str | Path
) -> list[AnnounceItem]:
    """Map notifications -> items (public-hygiene filter) and write the queue.

    Returns the items written so callers can assert on them without re-reading.
    """
    items = [notification_to_item(n) for n in notifications]
    _atomic_write_text(queue_path, codec.items_to_json_str(items))
    return items


def load_seen(path: str | Path) -> set[str]:
    """Load the persisted read-cursor (spoken ids). Missing/corrupt -> empty."""
    p = Path(path)
    if not p.exists():
        return set()
    try:
        return codec.seen_from_json_str(p.read_text(encoding="utf-8"))
    except Exception:
        return set()


def save_seen(path: str | Path, cursor) -> None:
    """Persist the read-cursor atomically so a reader never sees a partial file."""
    _atomic_write_text(path, codec.seen_to_json_str(cursor.seen))
