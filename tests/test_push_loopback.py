"""Build-time loopback: REAL exporter -> PUSH -> REAL on-device ability (Refs #7).

This is the push-transport sibling of test_exporter_loopback.py (which proves the
HTTP-pull path). It wires the *actual* PC-side exporter through the *actual* push
transport into the *actual* on-device ability pure-logic:

  seed state.db (5 org gates, incl. ci_unconfirmed_head_gate)
    -> core.export_queue                  (read-only DB -> §1.3 JSON file)
    -> push_once via LocalTransport       (atomic delivery to a "remote" path)
    -> [the runtime exposes that file as the ability's QUEUE_STORE]   <-- SEE BELOW
    -> background.ApprovalVoiceWatcher._drain_queue_once
         (items_from_raw -> ReadCursor dedup -> render_speech -> speak)

SCOPE / WHAT THIS DOES AND DOES NOT PROVE (be precise -- design.md M3.3.1):
  PROVES: (1) export -> push delivers the correct §1.3 bytes to the target path,
          atomically; (2) those bytes, *once in QUEUE_STORE*, render verbatim with
          a single interrupt and dedup on re-read -- the exact readout contract
          the deleted test_live_pull.py pinned, minus the (now removed) network
          fetch.
  DOES NOT PROVE: that the pushed target path *is* the ability's capability_worker
          storage. That mapping is the on-device OPEN QUESTION (the SDK reference
          documents storage by role, not by on-disk path; the ability cannot
          open() an arbitrary path). We model the runtime hand-off by copying the
          delivered bytes into the fake worker's storage under QUEUE_STORE; on real
          hardware, confirming the push target resolves to that storage is step 1
          (deploy/DEPLOY.md §4.3). Overclaiming here would mislead a reviewer.
"""
import asyncio
import importlib
import sys
import types
from pathlib import Path

from exporter_helpers import awaiting, seed_state_db

from pc_exporter import core
from pc_exporter.push import LocalTransport, PushState, push_once

_REPO_ROOT = Path(__file__).parent.parent


def _install_src_stubs():
    """Minimal `src.*` runtime stubs so `openhome_ability.background` imports.

    Mirrors deploy/build_zip.py's verify stub: the framework classes the ability
    subclasses/constructs do not live in this repo. `approval_voice` is the real
    bundled package (resolved by relative import), NOT stubbed.
    """
    if "src.agent.capability" in sys.modules:
        return
    src = types.ModuleType("src")
    agent = types.ModuleType("src.agent")
    capability = types.ModuleType("src.agent.capability")
    capability_worker = types.ModuleType("src.agent.capability_worker")
    main = types.ModuleType("src.main")

    class MatchingCapability:
        pass

    class CapabilityWorker:
        def __init__(self, *a, **k):
            pass

    class AgentWorker:
        pass

    capability.MatchingCapability = MatchingCapability
    capability_worker.CapabilityWorker = CapabilityWorker
    main.AgentWorker = AgentWorker
    src.agent = agent
    agent.capability = capability
    agent.capability_worker = capability_worker
    sys.modules.update({
        "src": src,
        "src.agent": agent,
        "src.agent.capability": capability,
        "src.agent.capability_worker": capability_worker,
        "src.main": main,
    })


def _install_bundle_aliases():
    """Resolve `openhome_ability.background`'s relative `from .approval_voice...`.

    In the deployed zip, `approval_voice/` is nested inside the wrap folder beside
    `background.py`. In the source tree the pure package lives at repo root, so we
    register the real submodules under the `openhome_ability.approval_voice.*`
    names the relative import expects (same objects -- no duplicate logic).
    """
    import approval_voice

    sys.modules.setdefault("openhome_ability.approval_voice", approval_voice)
    for sub in ("bridge", "poller", "renderer", "sample", "storage"):
        sys.modules.setdefault(
            f"openhome_ability.approval_voice.{sub}",
            importlib.import_module(f"approval_voice.{sub}"),
        )


_install_src_stubs()
_install_bundle_aliases()

from openhome_ability import background  # noqa: E402
from approval_voice.renderer import ONE_WAY_SUFFIX  # noqa: E402
from approval_voice.schema import GATES  # noqa: E402
from approval_voice.storage import QUEUE_STORE  # noqa: E402

# Prefix of the one-time daemon-startup announcement spoken at watch_queue start
# (background._speak_startup_announcement). watch_queue prepends it to the spoken
# list before any seed/pull/readout, so the gate readouts are cw.spoken[1:].
STARTUP_PREFIX = "approvalvoice デーモンが起動しました"


class _FakeCapabilityWorker:
    """In-memory stand-in for the OpenHome storage + speech API (all async)."""

    def __init__(self):
        self.storage = {}
        self.spoken = []
        self.interrupts = 0

    async def check_if_file_exists(self, name, temp):
        return name in self.storage

    async def read_file(self, name, temp):
        return self.storage[name]

    async def write_file(self, name, text, temp):
        self.storage[name] = text

    async def delete_file(self, name, temp):
        self.storage.pop(name, None)

    async def send_interrupt_signal(self):
        self.interrupts += 1

    async def speak(self, text):
        self.spoken.append(text)


class _FakeLog:
    def info(self, *a, **k):
        pass


class _StopLoop(Exception):
    """Raised from the patched sleep to break watch_queue's infinite loop."""


class _FakeSessionTasks:
    """Lets watch_queue run exactly `ticks` iterations, then stops the loop."""

    def __init__(self, ticks=1):
        self._remaining = ticks

    async def sleep(self, _seconds):
        self._remaining -= 1
        if self._remaining <= 0:
            raise _StopLoop

    def create(self, coro):
        coro.close()  # we drive watch_queue directly; don't schedule it twice


class _FakeWorker:
    def __init__(self, ticks=1):
        self.editor_logging_handler = _FakeLog()
        self.session_tasks = _FakeSessionTasks(ticks)


def _make_watcher(ticks=1):
    w = background.ApprovalVoiceWatcher()
    w.worker = _FakeWorker(ticks)
    w.capability_worker = _FakeCapabilityWorker()
    return w


def _run_watch_queue(watcher):
    """Drive the REAL watch_queue loop for `ticks` iterations (then _StopLoop)."""
    async def scenario():
        try:
            await watcher.watch_queue()
        except _StopLoop:
            pass

    asyncio.run(scenario())


def _seed_five_gate_db(db: Path) -> None:
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #100")},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("ci_green_merge_gate", "PR #101")},
        # folds onto escalation (must NOT become ci_merge -> no false CI-green claim)
        {"occurred_at": "2026-06-26T03:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("ci_unconfirmed_head_gate", "PR #102 merged at unconfirmed head")},
        {"occurred_at": "2026-06-26T04:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_to_user", "Issue #7 blocker")},
        {"occurred_at": "2026-06-26T05:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_reply_forward", "relay to worker")},
    ])


def test_push_loopback_exporter_to_ability_render(tmp_path):
    db = tmp_path / "state.db"
    _seed_five_gate_db(db)
    local_queue = tmp_path / "announce_queue.json"
    remote_drop = tmp_path / "devkit" / "announce_queue.json"  # simulated DevKit path

    # (1) exporter half: read-only DB -> §1.3 JSON file
    n = core.export_queue(db, local_queue)
    assert n == 5  # all 5 org gates survive the 5->4 fold (none dropped)

    # (2) push half: deliver the exported file to the (simulated) DevKit path.
    pushed = push_once(local_queue, str(remote_drop), LocalTransport(), PushState())
    assert pushed is True
    # The delivered bytes are byte-identical to the exporter output (no mangling).
    assert remote_drop.read_bytes() == local_queue.read_bytes()

    # (3) runtime hand-off (UNVERIFIED on hardware -- see module docstring): the
    # pushed file becomes the ability's QUEUE_STORE. We model that by loading the
    # delivered bytes into the fake worker's storage.
    watcher = _make_watcher()
    watcher.capability_worker.storage[QUEUE_STORE] = remote_drop.read_text(encoding="utf-8")

    async def scenario():
        await watcher._drain_queue_once(1)       # first read pass -> speak all
        first = list(watcher.capability_worker.spoken)
        await watcher._drain_queue_once(2)       # second pass -> dedup, no new speak
        return first

    first = asyncio.run(scenario())
    cw = watcher.capability_worker

    # 5 org gates folded onto the 4 §1.3 gates; all read aloud once, verbatim.
    assert len(first) == 5
    assert all(line.endswith(ONE_WAY_SUFFIX) for line in first)  # one-way reminder
    # escalation appears twice (worker_complete/ci_merge/escalation x2/reply_relay)
    import json
    gates = [i["gate"] for i in json.loads(cw.storage[QUEUE_STORE])]
    assert gates == ["worker_complete", "ci_merge", "escalation", "escalation", "reply_relay"]
    assert set(gates) == set(GATES)

    # the unconfirmed-head merge is read as an escalation reading the note
    # verbatim, NOT as a (false) "CI went green" merge announcement.
    assert "PR #102 merged at unconfirmed head" in first[2]
    assert "グリーン" not in first[2]

    # Interrupt sent exactly once before the batch (not per item, not per pass).
    assert cw.interrupts == 1
    # Second pass added nothing: read cursor suppressed the re-readout.
    assert cw.spoken == first


def test_push_loopback_idempotent_redelivery_then_new_gate(tmp_path):
    """Re-export with no DB change is NOT re-pushed; a new gate IS delivered and
    spoken exactly once (the push idempotency + ability dedup, end to end)."""
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #100")},
    ])
    local_queue = tmp_path / "announce_queue.json"
    remote_drop = tmp_path / "devkit" / "announce_queue.json"
    transport = LocalTransport()
    state = PushState()

    core.export_queue(db, local_queue)
    assert push_once(local_queue, str(remote_drop), transport, state) is True

    # Re-export with no DB change: bytes identical -> push is skipped (idempotent),
    # even though export rewrote the local file (new mtime).
    core.export_queue(db, local_queue)
    assert push_once(local_queue, str(remote_drop), transport, state) is False

    watcher = _make_watcher()
    watcher.capability_worker.storage[QUEUE_STORE] = remote_drop.read_text(encoding="utf-8")

    async def first_pass():
        await watcher._drain_queue_once(1)

    asyncio.run(first_pass())
    assert len(watcher.capability_worker.spoken) == 1  # only the one gate so far

    # A new gate lands in the DB -> re-export changes content -> push delivers it.
    from exporter_helpers import insert_event
    insert_event(db, "2026-06-26T06:00:00.000Z", "escalation_to_user", "Issue #9")
    core.export_queue(db, local_queue)
    assert push_once(local_queue, str(remote_drop), transport, state) is True

    # Runtime hand-off again, ability re-reads its (now updated) storage.
    watcher.capability_worker.storage[QUEUE_STORE] = remote_drop.read_text(encoding="utf-8")

    async def second_pass():
        await watcher._drain_queue_once(2)

    asyncio.run(second_pass())
    spoken = watcher.capability_worker.spoken
    assert len(spoken) == 2                         # the new gate, spoken once
    assert "Issue #9" in spoken[1]
    assert watcher.capability_worker.interrupts == 2  # one interrupt per readout batch


def test_watch_queue_reads_pushed_storage_no_network(tmp_path):
    # Drive the REAL daemon entry (watch_queue), not just _drain_queue_once: with
    # SMOKE_AUTOSEED off the loop must read whatever the PC pushed into QUEUE_STORE
    # and NOT touch the network (the storage-only reader contract). This is the
    # coverage the deleted test_live_pull.py used to give the loop body.
    db = tmp_path / "state.db"
    _seed_five_gate_db(db)
    local_queue = tmp_path / "announce_queue.json"
    core.export_queue(db, local_queue)

    watcher = _make_watcher(ticks=1)
    # Simulate the PC push already having landed the file in the ability storage.
    watcher.capability_worker.storage[QUEUE_STORE] = local_queue.read_text(encoding="utf-8")

    assert background.SMOKE_AUTOSEED is False     # production default (no self-seed)
    _run_watch_queue(watcher)

    cw = watcher.capability_worker
    assert cw.spoken[0].startswith(STARTUP_PREFIX)  # daemon-startup announcement first
    gates = cw.spoken[1:]
    assert len(gates) == 5                         # all 5 folded gates read aloud
    assert all(line.endswith(ONE_WAY_SUFFIX) for line in gates)
    assert cw.interrupts == 2                       # startup + the one readout batch


def test_watch_queue_self_seeds_when_smoke_autoseed_on(tmp_path, monkeypatch):
    # The trigger-free on-device smoke path: with SMOKE_AUTOSEED True the daemon
    # seeds the 4-gate sample into its own storage on startup and reads it -- no
    # push, no network, no trigger. Pins the self-seed branch of watch_queue.
    monkeypatch.setattr(background, "SMOKE_AUTOSEED", True)

    watcher = _make_watcher(ticks=1)
    assert QUEUE_STORE not in watcher.capability_worker.storage  # empty at start
    _run_watch_queue(watcher)

    cw = watcher.capability_worker
    assert QUEUE_STORE in cw.storage              # self-seeded its own storage
    import json
    assert {i["gate"] for i in json.loads(cw.storage[QUEUE_STORE])} == set(GATES)
    assert cw.spoken[0].startswith(STARTUP_PREFIX)  # startup announcement first
    assert len(cw.spoken[1:]) == 4                # canonical 4-gate sample read aloud
    assert cw.interrupts == 2                      # startup + the one readout batch
