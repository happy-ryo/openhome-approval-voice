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

# --- live pull transport (production PRIMARY, design.md §M3.3.1 (A), Refs #7) ---
#
# These are plain module constants on purpose, NOT environment variables: the
# OpenHome add-capability sandbox forbids the `os` module (platform access), so the
# on-device ability literally cannot read process environment variables. A config
# value the ability consumes must therefore be a constant here (the same reason
# POLL_SECONDS / SMOKE_AUTOSEED are constants). The PC-side exporter, which is NOT
# sandboxed, still takes its bind host/port from the environment — only the
# device-side consumer is pinned to a constant.
#
# (This comment deliberately avoids the literal `os` dot-attribute token: the
# sandbox lint regex-scans comments too, so naming the attribute would self-trip.)
#
# When PULL_ENABLED is True the daemon performs a read-only `requests.get` of
# PULL_URL each tick and writes the §1.3 body into QUEUE_STORE, then runs the
# unchanged read/dedup/render/speak pass. `requests` is the SANCTIONED outbound
# client (add-capability accepts it, HTTP 201; `urllib`/`http.client`/`socket` are
# rejected — §M3.1-s.7). The fetch is GET-only, so no body is ever sent back and
# the one-way invariant holds (tests/test_outbound_one_way.py allows GET, bans
# post/put/patch and GET-turned-POST).
#
# Fallback chain (design.md §M3.3.1): pull -> storage -> push. A failed pull
# (endpoint down, non-200, timeout, bad body) does NOT touch storage, so the
# daemon simply re-reads whatever is already in QUEUE_STORE — which a PC-side
# **push** (pc_exporter/push.py, the egress-failure fallback) may have delivered.
# All three transports converge on the same QUEUE_STORE read path.
PULL_ENABLED = True

# PC exporter LAN endpoint (pc_exporter serve, design.md §M3.3.1 (A)). The org's
# PC publishes the live §1.3 queue here; the DevKit daemon GETs it. Fixed constant
# (see above re: no env on device). Operator changes this one line if the PC LAN
# IP/port differs from the deployment default.
PULL_URL = "http://192.168.2.103:80/announce_queue.json"

# GET timeout (seconds). Short and bounded so a stalled endpoint cannot wedge the
# poll loop; on timeout the daemon keeps the existing storage and retries next tick.
PULL_TIMEOUT = 5.0
