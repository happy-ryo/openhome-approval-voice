"""approval-voice — on-device Background Ability (M3, sandbox-compliant).

OpenHome Background (always-on) Ability: it polls a name-addressed queue in the
agent's persistent file storage and reads each new `awaiting_user` gate aloud
**verbatim** via `capability_worker.speak()`.

WHY DevKit verbatim, not the cloud WebSocket (docs/design.md §M3): the cloud WS
path treats sent text as *user speech* and the agent's LLM paraphrases a reply —
it does NOT read the approval text verbatim. For an approval readout paraphrase
is a correctness risk; on-device `speak()` is direct TTS = verbatim.

ONE-WAY GUARANTEE (design.md §3.1): output is `speak()` only. This module never
calls `user_response()`, `run_io_loop()`, `run_confirmation_loop()`, or
`start_audio_recording()` — `tests/test_one_way.py` AST-scans this folder.
`send_interrupt_signal()` before speaking also prevents the daemon's own speech
from being transcribed back as user input — a second structural one-way guard.

SANDBOX COMPLIANCE (docs.openhome.com SDK reference): the bundle is statically
scanned, so this file uses no platform or file modules and no raw file reads. The
queue and the read-cursor are reached only through the agent's name-based file
storage (`capability_worker` async helpers), and the wire format is handled by
the shared `approval_voice.codec` (single serialization source).

Runtime contract (confirmed on-device by the requester, not asserted here):
- `capability_worker` async helpers `read_file/write_file/check_if_file_exists/
  delete_file(name, temp)` and `speak/send_interrupt_signal`;
- `temp=False` persistent storage is shared with the interactive seeder (main.py);
- the `def call()` + `session_tasks.create()` daemon shape (grounded on the
  shipped alarm-timer ability).
"""

from __future__ import annotations

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

from approval_voice.bridge import sample_queue_json
from approval_voice.codec import (
    items_from_json_str,
    seen_from_json_str,
    seen_to_json_str,
)
from approval_voice.poller import ReadCursor
from approval_voice.renderer import render_speech
from approval_voice.transport import (
    POLL_SECONDS,
    QUEUE_NAME,
    SAMPLE_NOTIFICATIONS,
    SEEN_NAME,
    SMOKE_BOOTSTRAP,
)

# Persistent storage scope so the read-cursor survives daemon restarts and is
# shared with the interactive seeder (main.py).
_PERSIST = False


class ApprovalVoiceWatcher(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None
    background_daemon_mode: bool = False

    # Do not change following tag of register capability
    # {{register capability}}

    async def _load_cursor(self) -> ReadCursor:
        """Build a ReadCursor from the persisted read-cursor (empty if none)."""
        if await self.capability_worker.check_if_file_exists(SEEN_NAME, _PERSIST):
            raw = await self.capability_worker.read_file(SEEN_NAME, _PERSIST)
            return ReadCursor(seen_from_json_str(raw))
        return ReadCursor()

    async def _save_cursor(self, cursor: ReadCursor) -> None:
        """Persist the read-cursor. JSON storage = delete + write (append corrupts)."""
        if await self.capability_worker.check_if_file_exists(SEEN_NAME, _PERSIST):
            await self.capability_worker.delete_file(SEEN_NAME, _PERSIST)
        await self.capability_worker.write_file(
            SEEN_NAME, seen_to_json_str(cursor.seen), _PERSIST
        )

    async def _maybe_bootstrap(self) -> None:
        """Smoke only: if the queue is absent, seed the sample so an *enabled*
        daemon speaks the gates on its own — no voice trigger / interactive entry
        / SSH needed (audible speak() is the only ground truth when device logs
        aren't reachable). Production (SMOKE_BOOTSTRAP=False) leaves seeding to
        the bridge. Idempotent: once the queue exists it never re-seeds, and the
        read-cursor prevents re-reading already-spoken gates."""
        if not SMOKE_BOOTSTRAP:
            return
        if await self.capability_worker.check_if_file_exists(QUEUE_NAME, _PERSIST):
            return
        await self.capability_worker.write_file(
            QUEUE_NAME, sample_queue_json(), _PERSIST
        )
        self.worker.editor_logging_handler.info(
            "[ApprovalVoice] SMOKE_BOOTSTRAP — seeded %d sample gate(s)"
            % len(SAMPLE_NOTIFICATIONS)
        )

    async def _read_aloud(self, fresh: list) -> list:
        """Verbatim readout: interrupt ONCE, then speak each. Returns utterances."""
        await self.capability_worker.send_interrupt_signal()
        spoken: list = []
        for item in fresh:
            text = render_speech(item)
            await self.capability_worker.speak(text)
            spoken.append(text)
        return spoken

    async def _poll_tick(self) -> list:
        """One poll iteration: read queue -> dedup -> verbatim readout. Read-only on
        the queue (only the local read-cursor is written). Returns utterances."""
        if not await self.capability_worker.check_if_file_exists(QUEUE_NAME, _PERSIST):
            return []
        items = items_from_json_str(
            await self.capability_worker.read_file(QUEUE_NAME, _PERSIST)
        )
        cursor = await self._load_cursor()
        fresh = cursor.unread(items)
        if not fresh:
            return []
        spoken = await self._read_aloud(fresh)
        cursor.mark_read(fresh)
        await self._save_cursor(cursor)
        return spoken

    async def watch_queue(self) -> None:
        """Infinite poll loop. One bad tick must never kill the daemon."""
        self.worker.editor_logging_handler.info(
            "[ApprovalVoice] background.py ACTIVE — polling %s every %ss"
            % (QUEUE_NAME, POLL_SECONDS)
        )
        try:
            await self._maybe_bootstrap()
        except Exception as e:
            self.worker.editor_logging_handler.info(
                "[ApprovalVoice] bootstrap error: %s" % e
            )
        while True:
            try:
                spoken = await self._poll_tick()
                if spoken:
                    self.worker.editor_logging_handler.info(
                        "[ApprovalVoice] read %d new gate(s) aloud" % len(spoken)
                    )
            except Exception as e:
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
