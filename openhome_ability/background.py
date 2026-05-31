"""approval-voice — on-device Background Ability (M3, real OpenHome).

This is the M3 *real* replacement for the M2 mock `ApprovalVoiceAbility`
(approval_voice/ability.py). It is an OpenHome Background (always-on) Ability
that runs on the DevKit, polls the shared announce queue, and reads each new
`awaiting_user` gate aloud **verbatim** via `capability_worker.speak()`.

Grounded against a real shipped background ability
(openhome-dev/abilities · community/alarm-timer/background.py): same imports,
`# {{register capability}}` marker, `call(self, worker, background_daemon_mode)`
signature, `session_tasks.create()` + `while True` + `session_tasks.sleep()`
loop, and `send_interrupt_signal()` **once** before speaking.

WHY (C) DevKit verbatim, not the cloud WebSocket (docs/design.md §M3): the
cloud WS path (`wss://app.openhome.com/websocket/voice-stream/{KEY}/{AGENT_ID}`)
treats sent text as *user speech* and the agent's LLM paraphrases a reply — it
does NOT read the approval text verbatim. For an approval readout, paraphrase is
a correctness risk. On-device `speak()` is direct TTS = verbatim.

ONE-WAY GUARANTEE (design.md §3.1): output is `speak()` only. This module never
calls `user_response()`, `run_io_loop()`, `run_confirmation_loop()`, or
`start_audio_recording()` — `tests/test_one_way.py` AST-scans this folder too.
`send_interrupt_signal()` before speaking also *prevents the daemon's own speech
from being transcribed back as user input* (per OpenHome background-abilities
docs) — a second structural guard for the one-way property.

The queue is read **read-only** (plain `open()`, the documented Local-Ability FS
pattern). The read-cursor is persisted to a *separate local* file on the device;
nothing is ever written back to the org (design.md §3.2 — zero side effect on
org state).
"""

from __future__ import annotations

import os
import sys

# The deploy bundle places the pure-logic `approval_voice` package next to this
# ability (see README deploy step) so the single source of truth for rendering /
# schema / dedup is reused, not duplicated (design.md §5).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

from approval_voice.bridge import load_queue
from approval_voice.poller import ReadCursor, load_seen, save_seen
from approval_voice.renderer import render_speech

# Transport (design.md §M3, Q2): a fixed local JSON file on the device, written
# atomically by the bridge and read here via plain open(). Paths/interval are
# env-configurable; defaults live under the user's OpenHome data dir.
_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".openhome", "approval_voice")
QUEUE_PATH = os.environ.get(
    "APPROVAL_VOICE_QUEUE", os.path.join(_DEFAULT_DIR, "announce_queue.json")
)
SEEN_PATH = os.environ.get(
    "APPROVAL_VOICE_SEEN", os.path.join(_DEFAULT_DIR, "announce_seen.json")
)
POLL_SECONDS = float(os.environ.get("APPROVAL_VOICE_POLL_SECONDS", "15"))


class ApprovalVoiceWatcher(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None
    background_daemon_mode: bool = False

    # Do not change following tag of register capability
    # {{register capability}}

    async def _read_aloud(self, fresh: list) -> None:
        """Verbatim readout of new gates. Interrupt ONCE, then speak each."""
        # Interrupt once before speaking (never inside the loop) — also stops the
        # daemon's output being re-transcribed as user input (one-way guard).
        await self.capability_worker.send_interrupt_signal()
        for item in fresh:
            await self.capability_worker.speak(render_speech(item))

    async def watch_queue(self) -> None:
        """Infinite poll loop: detect unread gates -> verbatim readout. Read-only."""
        self.worker.editor_logging_handler.info(
            "[ApprovalVoice] background.py ACTIVE — polling %s every %ss"
            % (QUEUE_PATH, POLL_SECONDS)
        )
        while True:
            try:
                if os.path.exists(QUEUE_PATH):
                    items = load_queue(QUEUE_PATH)               # read-only
                    cursor = ReadCursor(load_seen(SEEN_PATH))    # local cursor
                    fresh = cursor.unread(items)
                    if fresh:
                        self.worker.editor_logging_handler.info(
                            "[ApprovalVoice] %d new gate(s) -> reading aloud" % len(fresh)
                        )
                        await self._read_aloud(fresh)
                        cursor.mark_read(fresh)
                        save_seen(SEEN_PATH, cursor)             # persist locally
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
