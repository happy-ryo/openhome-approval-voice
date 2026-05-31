"""Wire-format codec — the single serialization source (design.md §4, §5).

Converts between the on-wire JSON text and domain objects. No filesystem access
lives here: callers hand in / receive **strings**, so the file layer
(`approval_voice.fileio`, PC-side) and the on-device `capability_worker` layer
(`openhome_ability.background`) share one (de)serialization implementation
instead of duplicating it.

Bundle note: this module ships inside the on-device ability bundle, which is
statically scanned by the OpenHome runtime. The serialization library is
therefore imported **lazily inside each function** (never at module scope) to
stay within the bundle's import rules; nothing here touches device system APIs.
"""

from __future__ import annotations

from .schema import AnnounceItem


def items_from_json_str(raw: str) -> list[AnnounceItem]:
    """Parse the announce-queue text (an array) into AnnounceItems."""
    import json

    return [AnnounceItem.from_dict(entry) for entry in json.loads(raw)]


def items_to_json_str(items: list[AnnounceItem]) -> str:
    """Serialize AnnounceItems to the announce-queue text (stable, UTF-8)."""
    import json

    return json.dumps([item.to_dict() for item in items], ensure_ascii=False, indent=2)


def seen_from_json_str(raw: str) -> set[str]:
    """Parse the read-cursor text (an array of ids). Corrupt -> empty set."""
    import json

    try:
        return {str(x) for x in json.loads(raw)}
    except Exception:
        # A corrupt cursor must never crash the always-on daemon.
        return set()


def seen_to_json_str(seen: set[str]) -> str:
    """Serialize the read-cursor (spoken ids) to text, sorted for stability."""
    import json

    return json.dumps(sorted(seen), ensure_ascii=False, indent=2)
