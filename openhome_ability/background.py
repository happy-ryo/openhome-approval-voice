"""approval-voice — on-device Background Daemon ability (M3.1, sandbox compliant).

This is the M3 *real* replacement for the M2 mock `ApprovalVoiceAbility`
(approval_voice/ability.py). It is an OpenHome Background Daemon ability that runs
on the DevKit, polls a persistent storage file for the announce queue, and reads
each new `awaiting_user` gate aloud **verbatim** via `capability_worker.speak()`.

STORAGE-ONLY READER (design.md §M3.3.1, push transport — Refs #7). The daemon
**only reads its own `capability_worker` storage** (`QUEUE_STORE`); it makes NO
outbound network call. An earlier revision pulled the queue from the PC exporter
over an outbound HTTP GET (`urllib`), but the OpenHome add-capability sandbox was
empirically found to **reject `urllib`** (HTTP 400 — denylisted, design.md
§M3.1-s.7 / §M3.3.1). So the production transport is inverted: the **PC pushes**
the §1.3 queue file into the DevKit (scp/sftp, `pc_exporter/push.py`) and this
daemon reads whatever now sits in its storage — the unchanged read/dedup/render/
speak pass below. The read mechanism is exactly the §M3.1 storage path; only the
delivery direction changed (pull -> push), so nothing here imports a network
module and the sandbox denylist can never be tripped by this file.

> OPEN QUESTION (design.md §M3.3.1, requires on-device investigation): whether the
> path the PC pushes to *is* this ability's `capability_worker` storage location
> is UNVERIFIED. The SDK reference documents storage by *role* ("user data
> storage, shared across abilities" for the `in_ability_directory=False` arg) but
> publishes no concrete on-disk path, and low-level file access is sandbox-banned
> so the daemon cannot read an arbitrary path itself. `pc_exporter/push.py` is
> transport-only (the operator supplies `--target host:path`); confirming that
> target maps onto this storage is the first on-device step (deploy/DEPLOY.md §4.3).

LIFECYCLE (design.md §M3.1-sandbox.6): a background_daemon **starts automatically
on session and has NO trigger words** ("No triggers for this ability" on the
Dashboard is expected, not a bug). It therefore cannot rely on the interactive
`main.py` being voice-triggered to seed the queue. So for the on-device smoke this
daemon **self-seeds** the canonical 4-gate sample on startup when
`SMOKE_AUTOSEED` is true, then reads it aloud — no trigger, no SSH. Issue #7 sets
`SMOKE_AUTOSEED = False` so the daemon reads only real pushed exporter data.

Grounded against a real shipped background ability
(openhome-dev/abilities · community/alarm-timer/background.py): same framework
imports, `# {{register capability}}` marker, `call(self, worker,
background_daemon_mode)` signature, `session_tasks.create()` + `while True` +
`session_tasks.sleep()` loop, `send_interrupt_signal()` **once** before speaking,
and persistence via the `capability_worker` storage API (`read_file` /
`write_file` / `check_if_file_exists` / `delete_file`, all async, 2nd arg False).

WHY (C) DevKit verbatim, not the cloud WebSocket (docs/design.md §M3): the cloud
WS path treats sent text as *user speech* and the agent's LLM paraphrases a reply
— it does NOT read the approval text verbatim. For an approval readout, paraphrase
is a correctness risk. On-device `speak()` is direct TTS = verbatim.

ONE-WAY GUARANTEE (design.md §3.1): output is `speak()` only. This module never
captures user input, and — with the outbound GET removed — never makes ANY
network call, so there is structurally no return channel to the PC.
`tests/test_one_way.py` AST-scans this folder; `tests/test_outbound_one_way.py`
additionally pins that no outbound write verb (or `urlopen`/`Request`) appears.
`send_interrupt_signal()` before speaking also *prevents the daemon's own speech
from being transcribed back as user input* (per OpenHome background-abilities
docs) — a second structural guard for the one-way property.

M3.1 sandbox compliance (design.md §M3.1): the OpenHome add-capability static scan
rejects low-level platform access, module-scope data-encoding imports, raw file
access and low-level signal handling. So:
  - file coordination uses the **storage-name-based async `capability_worker`
    API** (no file paths, no raw file access; design.md §M3.1);
  - data encoding is imported **inside method bodies**, not at module scope;
  - the pure-logic `approval_voice` package is resolved by **relative import**
    (`from .approval_voice...`) with no execution-path rewriting. The ability
    bundle is loaded as a package (wrap folder), so a bundled sub-package resolves
    via relative import — proven by openhome-dev/abilities ·
    dungeon-master-voice's `from .dm_personalities`.
"""

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

# Resolve the bundled pure logic (single source of truth) by relative import.
from .approval_voice.bridge import items_from_raw, notifications_to_payload
from .approval_voice.poller import ReadCursor, seen_from_raw, seen_to_payload
from .approval_voice.renderer import render_speech
from .approval_voice.sample import SAMPLE_NOTIFICATIONS, SMOKE_AUTOSEED
from .approval_voice.storage import (
    POLL_SECONDS,
    QUEUE_STORE,
    SEEN_STORE,
)


class ApprovalVoiceWatcher(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None
    background_daemon_mode: bool = False

    # Do not change following tag of register capability
    # {{register capability}}

    def _log(self, msg: str) -> None:
        """Route a diagnostic line to the OpenHome editor log (Open In Editor ->
        log tab). Verbose on purpose for the M3.1 on-device bring-up (Refs #11);
        trim once the readout is confirmed on hardware."""
        self.worker.editor_logging_handler.info("[ApprovalVoice] %s" % msg)

    async def _load_seen(self) -> set:
        """Load the persisted read-cursor from storage (missing/corrupt -> empty)."""
        # method-local import: a module-scope encode import is banned by the sandbox.
        import json

        if not await self.capability_worker.check_if_file_exists(SEEN_STORE, False):
            return set()
        try:
            raw = await self.capability_worker.read_file(SEEN_STORE, False)
            return seen_from_raw(json.loads(raw))
        except Exception as e:
            # A corrupt cursor must not crash the daemon; treat as nothing-seen.
            # repr(e) gives "ExceptionType('msg')" — type + message without a
            # forbidden `traceback` import or a `.__name__` dunder access.
            self._log("load_seen error (treating as empty): %s" % repr(e))
            return set()

    async def _save_seen(self, cursor: ReadCursor) -> None:
        """Persist the read-cursor (delete-then-write, the documented OpenHome
        pattern that avoids a half-written / appended cursor file)."""
        import json

        payload = json.dumps(seen_to_payload(cursor), ensure_ascii=False, indent=2)
        if await self.capability_worker.check_if_file_exists(SEEN_STORE, False):
            await self.capability_worker.delete_file(SEEN_STORE, False)
        await self.capability_worker.write_file(SEEN_STORE, payload, False)
        self._log("saved read-cursor (%d id(s))" % len(seen_to_payload(cursor)))

    async def _smoke_seed(self) -> None:
        """On-device smoke: write the 4-gate sample to storage + reset the cursor.

        A background_daemon has no trigger, so the daemon seeds itself instead of
        waiting on the interactive entry. Resetting the cursor means every session
        (re)start yields a fresh full readout (re-test = restart). Issue #7 turns
        `SMOKE_AUTOSEED` off so this no-ops and the daemon reads real pushed data.
        """
        import json

        self._log("smoke_seed: start")
        payload = notifications_to_payload(SAMPLE_NOTIFICATIONS)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        self._log("smoke_seed: built payload (%d gate(s), %d chars)"
                  % (len(payload), len(text)))
        seen_exists = await self.capability_worker.check_if_file_exists(SEEN_STORE, False)
        self._log("smoke_seed: seen exists=%s" % seen_exists)
        if seen_exists:
            await self.capability_worker.delete_file(SEEN_STORE, False)
            self._log("smoke_seed: reset read-cursor")
        queue_exists = await self.capability_worker.check_if_file_exists(QUEUE_STORE, False)
        self._log("smoke_seed: queue exists=%s" % queue_exists)
        if queue_exists:
            await self.capability_worker.delete_file(QUEUE_STORE, False)
        await self.capability_worker.write_file(QUEUE_STORE, text, False)
        self._log("smoke_seed: wrote %d gate(s) to %s -> end"
                  % (len(payload), QUEUE_STORE))

    async def _read_aloud(self, fresh: list) -> None:
        """Verbatim readout of new gates. Interrupt ONCE, then speak each."""
        # Interrupt once before speaking (never inside the loop) — also stops the
        # daemon's output being re-transcribed as user input (one-way guard).
        self._log("read_aloud: start (%d gate(s)); sending interrupt" % len(fresh))
        await self.capability_worker.send_interrupt_signal()
        self._log("read_aloud: interrupt sent")
        for i, item in enumerate(fresh, start=1):
            text = render_speech(item)
            self._log("read_aloud: speak %d/%d (gate=%s, %d chars)"
                      % (i, len(fresh), item.gate, len(text)))
            await self.capability_worker.speak(text)
            self._log("read_aloud: speak %d/%d done" % (i, len(fresh)))
        self._log("read_aloud: end")

    async def _drain_queue_once(self, tick: int) -> None:
        """One read pass: load QUEUE_STORE, speak any unread gates, persist cursor.

        Read-only with respect to the org: it reads the queue from local storage
        (whatever the PC last pushed in), speaks fresh gates and advances the
        *local* read cursor only (§3.2). Split out of the poll loop so the read
        pass stays independently testable.
        """
        import json

        exists = await self.capability_worker.check_if_file_exists(QUEUE_STORE, False)
        if not exists:
            if tick <= 3:
                self._log("poll tick=%d: queue_exists=False (nothing to read)" % tick)
            return
        raw = await self.capability_worker.read_file(QUEUE_STORE, False)
        items = items_from_raw(json.loads(raw))          # read-only
        seen = await self._load_seen()                    # local cursor
        cursor = ReadCursor(seen)
        fresh = cursor.unread(items)
        if tick <= 3 or fresh:
            self._log("poll tick=%d: queue_exists=True raw=%dchars items=%d "
                      "seen=%d fresh=%d"
                      % (tick, len(raw), len(items), len(seen), len(fresh)))
        if fresh:
            await self._read_aloud(fresh)
            cursor.mark_read(fresh)
            await self._save_seen(cursor)                 # persist locally

    async def watch_queue(self) -> None:
        """Infinite poll loop: read local storage -> detect unread gates -> readout.

        No network step: the PC-side push (pc_exporter/push.py) keeps QUEUE_STORE
        fresh; the daemon just re-reads its own storage each tick.
        """
        self._log("watch_queue: task started (SMOKE_AUTOSEED=%s, "
                  "background_daemon_mode=%s)"
                  % (SMOKE_AUTOSEED, self.background_daemon_mode))
        if SMOKE_AUTOSEED:
            try:
                await self._smoke_seed()
            except Exception as e:  # seeding must never prevent the daemon starting
                self._log("smoke autoseed error: %s" % repr(e))
        else:
            self._log("watch_queue: SMOKE_AUTOSEED is False -> no autoseed")
        self._log("background.py ACTIVE — polling storage %s every %ss (push transport)"
                  % (QUEUE_STORE, POLL_SECONDS))
        tick = 0
        while True:
            tick += 1
            try:
                await self._drain_queue_once(tick)
            except Exception as e:  # never let one bad tick kill the daemon
                self._log("poll error (tick=%d): %s" % (tick, repr(e)))
            await self.worker.session_tasks.sleep(POLL_SECONDS)

    def call(self, worker: AgentWorker, background_daemon_mode: bool):
        self.worker = worker
        self.background_daemon_mode = background_daemon_mode
        self.capability_worker = CapabilityWorker(self.worker)
        self._log("call() entered (background_daemon_mode=%s, SMOKE_AUTOSEED=%s) "
                  "— creating watch_queue task" % (background_daemon_mode, SMOKE_AUTOSEED))
        self.worker.session_tasks.create(self.watch_queue())
