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

# Outbound announce source (design.md §M3.3.1 / §M3.1-s.5) — the live PC->DevKit
# pull. When set, the daemon GETs this URL every poll and writes the response to
# QUEUE_STORE before the normal read/dedup/render/speak pass (one-way: GET only,
# never a write-back to the PC). None = disabled: the daemon reads only what is
# already in its own storage (the M3.1 self-seed smoke path).
#
# Set it to the PC exporter URL to enable the live pull, e.g.
#   ANNOUNCE_SOURCE_URL = "http://192.168.1.20:8000/announce_queue.json"
# It is NOT hardcoded to a host: this single named constant is the one config
# point (an env var would need os.*, which the sandbox forbids — §M3.1-s.1). Only
# http(s) is honored (approval_voice/source.py rejects file://-style URLs so the
# pull can never become a local-file read).
ANNOUNCE_SOURCE_URL = None

# Per-fetch HTTP timeout (seconds). Kept well under POLL_SECONDS so an asleep or
# unreachable PC pauses the daemon only briefly before the tick is skipped (the
# blocking GET freezes the event loop up to this long — see background.py).
FETCH_TIMEOUT_SECONDS = 5.0

# Upper bound on a fetched queue body (bytes). The announce queue is a handful of
# short gates, so 1 MiB is generous; a misconfigured URL returning a huge / HTML
# 200 is treated as a failed fetch (skipped tick) rather than read into memory.
MAX_FETCH_BYTES = 1_048_576
