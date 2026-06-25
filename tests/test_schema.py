"""Schema invariants (design.md §1.3).

`AnnounceItem.from_dict` filters incoming dicts to known fields using the explicit
`ITEM_FIELDS` tuple instead of introspecting the dataclass field map — the
OpenHome add-capability sandbox rejects dunder attribute access (design.md
§M3.1-sandbox). This test is the sync guard: it fails if a dataclass field is
added/renamed without updating `ITEM_FIELDS`, so from_dict can't silently drop it.
Tests are not part of the deployed bundle, so they may introspect the dataclass.
"""

import dataclasses

from approval_voice.schema import ITEM_FIELDS, AnnounceItem


def test_item_fields_matches_dataclass_fields():
    assert ITEM_FIELDS == tuple(f.name for f in dataclasses.fields(AnnounceItem))


def test_from_dict_drops_unknown_fields():
    item = AnnounceItem.from_dict(
        {"id": "7", "gate": "ci_merge", "title": "t", "question": "q",
         "leaked_internal": "x"}  # extra field must be dropped
    )
    assert item.gate == "ci_merge"
    assert "leaked_internal" not in item.to_dict()
