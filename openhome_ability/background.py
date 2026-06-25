"""approval-voice — on-device Background Daemon ability (M3.1, sandbox compliant).

This is the M3 *real* replacement for the M2 mock `ApprovalVoiceAbility`
(approval_voice/ability.py). It is an OpenHome Background Daemon ability that runs
on the DevKit, polls a persistent storage file for the announce queue, and reads
each new `awaiting_user` gate aloud **verbatim** via `capability_worker.speak()`.

LIFECYCLE (design.md §M3.1-sandbox.6): a background_daemon **starts automatically
on session and has NO trigger words** ("No triggers for this ability" on the
Dashboard is expected, not a bug). It therefore cannot rely on the interactive
`main.py` being voice-triggered to seed the queue. So for the on-device smoke this
daemon **self-seeds** the canonical 4-gate sample on startup when
`SMOKE_AUTOSEED` is true, then reads it aloud — no trigger, no SSH. Issue #7 sets
`SMOKE_AUTOSEED = False` so the daemon reads only real exporter data.

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
captures user input — `tests/test_one_way.py` AST-scans this folder too.
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

from __future__ import annotations

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

# Resolve the bundled pure logic (single source of truth) by relative import.
from .approval_voice.bridge import items_from_raw, notifications_to_payload
from .approval_voice.poller import ReadCursor, seen_from_raw, seen_to_payload
from .approval_voice.renderer import render_speech
from .approval_voice.sample import SAMPLE_NOTIFICATIONS, SMOKE_AUTOSEED
from .approval_voice.storage import POLL_SECONDS, QUEUE_STORE, SEEN_STORE


class ApprovalVoiceWatcher(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None
    background_daemon_mode: bool = False

    # Do not change following tag of register capability
    # {{register capability}}

    async def _load_seen(self) -> set:
        """Load the persisted read-cursor from storage (missing/corrupt -> empty)."""
        # method-local import: a module-scope encode import is banned by the sandbox.
        import json

        if not await self.capability_worker.check_if_file_exists(SEEN_STORE, False):
            return set()
        try:
            raw = await self.capability_worker.read_file(SEEN_STORE, False)
            return seen_from_raw(json.loads(raw))
        except Exception:
            # A corrupt cursor must not crash the daemon; treat as nothing-seen.
            return set()

    async def _save_seen(self, cursor: ReadCursor) -> None:
        """Persist the read-cursor (delete-then-write, the documented OpenHome
        pattern that avoids a half-written / appended cursor file)."""
        import json

        payload = json.dumps(seen_to_payload(cursor), ensure_ascii=False, indent=2)
        if await self.capability_worker.check_if_file_exists(SEEN_STORE, False):
            await self.capability_worker.delete_file(SEEN_STORE, False)
        await self.capability_worker.write_file(SEEN_STORE, payload, False)

    async def _smoke_seed(self) -> None:
        """On-device smoke: write the 4-gate sample to storage + reset the cursor.

        A background_daemon has no trigger, so the daemon seeds itself instead of
        waiting on the interactive entry. Resetting the cursor means every session
        (re)start yields a fresh full readout (re-test = restart). Issue #7 turns
        `SMOKE_AUTOSEED` off so this no-ops and the daemon reads real data only.
        """
        import json

        payload = notifications_to_payload(SAMPLE_NOTIFICATIONS)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if await self.capability_worker.check_if_file_exists(SEEN_STORE, False):
            await self.capability_worker.delete_file(SEEN_STORE, False)
        if await self.capability_worker.check_if_file_exists(QUEUE_STORE, False):
            await self.capability_worker.delete_file(QUEUE_STORE, False)
        await self.capability_worker.write_file(QUEUE_STORE, text, False)
        self.worker.editor_logging_handler.info(
            "[ApprovalVoice] smoke autoseed -> %d gate(s) written to %s"
            % (len(payload), QUEUE_STORE)
        )

    async def _read_aloud(self, fresh: list) -> None:
        """Verbatim readout of new gates. Interrupt ONCE, then speak each."""
        # Interrupt once before speaking (never inside the loop) — also stops the
        # daemon's output being re-transcribed as user input (one-way guard).
        await self.capability_worker.send_interrupt_signal()
        for item in fresh:
            await self.capability_worker.speak(render_speech(item))

    async def watch_queue(self) -> None:
        """Infinite poll loop: detect unread gates -> verbatim readout. Read-only."""
        import json

        if SMOKE_AUTOSEED:
            try:
                await self._smoke_seed()
            except Exception as e:  # seeding must never prevent the daemon starting
                self.worker.editor_logging_handler.info(
                    "[ApprovalVoice] smoke autoseed error: %s" % e
                )
        self.worker.editor_logging_handler.info(
            "[ApprovalVoice] background.py ACTIVE — polling storage %s every %ss"
            % (QUEUE_STORE, POLL_SECONDS)
        )
        while True:
            try:
                if await self.capability_worker.check_if_file_exists(QUEUE_STORE, False):
                    raw = await self.capability_worker.read_file(QUEUE_STORE, False)
                    items = items_from_raw(json.loads(raw))      # read-only
                    cursor = ReadCursor(await self._load_seen())  # local cursor
                    fresh = cursor.unread(items)
                    if fresh:
                        self.worker.editor_logging_handler.info(
                            "[ApprovalVoice] %d new gate(s) -> reading aloud" % len(fresh)
                        )
                        await self._read_aloud(fresh)
                        cursor.mark_read(fresh)
                        await self._save_seen(cursor)             # persist locally
            except Exception as e:  # never let one bad tick kill the daemon
                self.worker.editor_logging_handler.info(
                    "[ApprovalVoice] poll error: %s" % e
                )
            await self.worker.session_tasks.sleep(POLL_SECONDS)

    def call(self, worker: AgentWorker, background_daemon_mode: bool):
        self.worker = worker
        self.background_daemon_mode = background_daemon_mode
        self.capability_worker = CapabilityWorker(self.worker)
        self.worker.editor_logging_handler.info(
            "[ApprovalVoice] background.py call() — starting watch_queue task"
        )
        self.worker.session_tasks.create(self.watch_queue())
