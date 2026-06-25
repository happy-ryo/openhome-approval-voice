"""Read-only state -> announce-queue bridge (design.md §1.2 step ②, §5).

Task deliverable (1): receive an `awaiting_user` notification (gate kind +
options) and map it to a §1.3 AnnounceItem.

This is the component shared with the sister project as the future
`openhome-org-voice-core` exporter base. It is strictly **read-only / one-way**
(design.md §3.2): it maps a pending-decision notification into a queue item and
never writes back to the org / holds no return channel.

M3.1 sandbox compliance (design.md §M3.1): the OpenHome add-capability static
scan rejects low-level platform access, module-scope data-encoding imports and
raw file access. So the bridge stays **pure logic** with no file I/O and no
data-encoding: serialization and storage reads/writes live in the ability layer
(`openhome_ability/background.py` / `main.py`) via the `capability_worker`
storage API, and the bridge only ever handles already-decoded dicts / lists
(`items_from_raw` / `notifications_to_payload`).

M2 note (§4): the org side is replaced by a *hand-written* notification dict.
The input shape here is deliberately conceptual — it does NOT mirror any real
claude-org state schema (public hygiene). M3 replaces this input with a real,
read-only export from live state.
"""

from .schema import AnnounceItem


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


def items_from_raw(data: list) -> list[AnnounceItem]:
    """Parse an already-decoded queue array (list of dicts) into AnnounceItems.

    The ability reads the queue string via `capability_worker.read_file(...)` and
    decodes it with a method-local JSON parse (module-scope encoding imports are
    banned by the sandbox); this pure function then turns the decoded list into
    validated items. Replaces the file-reading half of the old `load_queue`.
    """
    return [AnnounceItem.from_dict(entry) for entry in data]


def items_to_payload(items: list[AnnounceItem]) -> list[dict]:
    """Serialize AnnounceItems into an encode-ready list of dicts.

    The ability turns the returned list into a string with a method-local JSON
    dump and writes it via `capability_worker.write_file(...)`. Replaces the
    file-writing half of the old `export_queue`.
    """
    return [item.to_dict() for item in items]


def notifications_to_payload(notifications: list[dict]) -> list[dict]:
    """Map raw notifications -> an encode-ready queue payload (public-hygiene applied).

    Convenience for the seed path (`background.py` self-seeds the canonical
    4-gate sample into storage on startup so the on-device smoke needs no trigger
    or SSH): each notification is run through `notification_to_item` so the §1.3
    whitelist + gate validation apply to seeded data too, then reduced to plain dicts.
    """
    return items_to_payload([notification_to_item(n) for n in notifications])
