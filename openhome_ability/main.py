"""approval-voice — interactive entry (M3, sandbox-compliant).

The real readout work happens in the always-on daemon (background.py). This
interactive entry exists to (1) seed the canonical smoke sample into the agent's
name-based file storage via `capability_worker.write_file()` so the daemon picks
it up on its next poll (replaces the old SSH-placed local queue file — no device
shell needed), and (2) speak one status line so a human can confirm the ability
is installed.

ONE-WAY GUARANTEE (design.md §3.1): output-only + a storage write. On trigger it
seeds the sample, speaks one status line, and returns control. It NEVER captures
input — no `user_response()`, `run_io_loop()`, `run_confirmation_loop()`, or
`start_audio_recording()`. `tests/test_one_way.py` AST-scans this file too.

SANDBOX COMPLIANCE: no platform or file modules and no raw file reads; storage is
name-based via `capability_worker`, serialized through the shared
`approval_voice.codec`. Runtime helper signatures are confirmed on-device by the
requester (see background.py), not asserted here.
"""

from __future__ import annotations

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

from approval_voice.bridge import notification_to_item
from approval_voice.codec import items_to_json_str
from approval_voice.transport import QUEUE_NAME, SAMPLE_NOTIFICATIONS

_PERSIST = False  # share persistent storage with the daemon (background.py)

STATUS_LINE = (
    "承認音声リーダーは常駐で動作しています。"
    "サンプルの承認待ちを投入しました。まもなく順に読み上げます。"
    "返事は端末でお願いします。"
)


class ApprovalVoiceStatus(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None

    # Do not change following tag of register capability
    # {{register capability}}

    async def _seed_sample(self) -> None:
        """Write the canonical 4-gate sample to the queue (delete + write)."""
        items = [notification_to_item(n) for n in SAMPLE_NOTIFICATIONS]
        if await self.capability_worker.check_if_file_exists(QUEUE_NAME, _PERSIST):
            await self.capability_worker.delete_file(QUEUE_NAME, _PERSIST)
        await self.capability_worker.write_file(
            QUEUE_NAME, items_to_json_str(items), _PERSIST
        )

    async def announce_status(self) -> None:
        await self._seed_sample()
        await self.capability_worker.speak(STATUS_LINE)
        # Return to the normal conversation flow; never enter an input/confirm loop.
        self.capability_worker.resume_normal_flow()

    def call(self, worker: AgentWorker):
        self.worker = worker
        self.capability_worker = CapabilityWorker(self.worker)
        self.worker.editor_logging_handler.info(
            "[ApprovalVoice] main.py ACTIVE — seeding sample + speaking status"
        )
        self.worker.session_tasks.create(self.announce_status())
