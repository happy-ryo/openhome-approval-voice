"""Shared announce-queue item schema (design.md §1.3).

This is the *cross-project contract* shared with the sister project
openhome-ambient-announcer (future `openhome-org-voice-core`). Do NOT add
fields or branch the schema per-project: a project that has no choices simply
emits an empty `options` list. Keeping this stable is what prevents the two
M2 efforts from diverging.

Public hygiene: `gate`/`subject` are *conceptual labels* only. No internal
org state schema, internal identifiers, machine paths, or hook names belong
in an AnnounceItem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Explicit field list — used instead of dataclass field-introspection because the
# OpenHome capability sandbox forbids dunder attribute access in bundled code.
# Keep in sync with the dataclass fields declared below.
_ITEM_FIELDS = ("id", "gate", "title", "question", "subject", "options", "created_at")

# The four awaiting_user gates the Secretary stops on (design.md §2).
GATE_WORKER_COMPLETE = "worker_complete"
GATE_CI_MERGE = "ci_merge"
GATE_ESCALATION = "escalation"
GATE_REPLY_RELAY = "reply_relay"

GATES = (
    GATE_WORKER_COMPLETE,
    GATE_CI_MERGE,
    GATE_ESCALATION,
    GATE_REPLY_RELAY,
)


@dataclass
class AnnounceItem:
    """One "waiting decision" to be read aloud. Maps 1:1 to a queue entry."""

    id: str                       # stable unique id — dedup / read-cursor key
    gate: str                     # one of GATES (conceptual label)
    title: str                    # short headline, spoken first
    question: str                 # the body / what is being waited on
    subject: str = ""             # conceptual label of the target (no internal id)
    options: list[str] = field(default_factory=list)  # choices; [] if none
    created_at: str = ""          # ISO8601; stamped by the bridge, not generated here

    def __post_init__(self) -> None:
        if self.gate not in GATES:
            raise ValueError(
                f"unknown gate {self.gate!r}; expected one of {GATES}"
            )

    @classmethod
    def from_dict(cls, data: dict) -> "AnnounceItem":
        known = {f: data[f] for f in _ITEM_FIELDS if f in data}
        return cls(**known)

    def to_dict(self) -> dict:
        # Explicit dict (no dataclasses.asdict / dunder introspection): only the
        # whitelisted §1.3 fields cross the wire (public hygiene, single choke).
        return {
            "id": self.id,
            "gate": self.gate,
            "title": self.title,
            "question": self.question,
            "subject": self.subject,
            "options": list(self.options),
            "created_at": self.created_at,
        }
