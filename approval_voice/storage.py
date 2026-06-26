"""OpenHome persistent-storage identifiers (M3.1 sandbox compliant).

The OpenHome add-capability static sandbox scan rejects low-level platform
access, module-scope data-encoding imports, raw file access and low-level signal
handling (see docs/design.md §M3.1 for the exact list). The ability therefore
coordinates files by **storage name** through the `capability_worker` API
(`read_file` / `write_file` / `check_if_file_exists` / `delete_file`; all async,
2nd arg False = persistent), not by file path.

This module holds only the storage names for the read-aloud queue and the
read-cursor (plain string constants, no I/O), so background.py and main.py share
one **single source of truth** and cannot drift apart.

> Note: schema/poller/bridge/renderer are storage-agnostic logic shared with the
> sister project; this storage.py is approval-voice app config (the sister has
> its own names) — the same app-specific role speak.py / ability.py already play.
"""

# Read-aloud queue (a JSON array of §1.3 items).
QUEUE_STORE = "announce_queue.json"

# Read-cursor (a JSON array of spoken ids). Kept locally on the ability side and
# never written back to the org (zero side effect, design.md §3.2).
SEEN_STORE = "announce_seen.json"

# Poll interval (seconds). A fixed value — env lookups need platform access that
# the sandbox forbids. 10-30s is the documented norm.
POLL_SECONDS = 15.0

# NOTE (design.md §M3.3.1, Refs #7): there is intentionally NO announce-source
# URL here. An earlier revision had the daemon GET the queue from the PC exporter
# over `urllib`, but the OpenHome add-capability sandbox was found to reject
# `urllib` (HTTP 400 — denylisted, §M3.1-s.7). The production transport is now a
# **PC-side push** (`pc_exporter/push.py` scp/sftp's the §1.3 queue file into the
# DevKit); the daemon makes no network call and simply reads whatever now sits in
# QUEUE_STORE. So this module holds only storage names + the poll interval — no
# network config, which also keeps the bundle clear of the sandbox denylist.
