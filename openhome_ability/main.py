"""approval-voice — interactive entry (M3, real OpenHome).

The real readout work happens in the always-on daemon (background.py). This
`main.py` exists only to satisfy the OpenHome ability convention (interactive
trigger + background watcher, as in community/alarm-timer) and to give a human a
voice way to confirm the ability is installed.

ONE-WAY GUARANTEE (design.md §3.1): output-only. On trigger it speaks one status
line and immediately returns control. It NEVER captures input — no
`user_response()`, `run_io_loop()`, `run_confirmation_loop()`, or
`start_audio_recording()`. `tests/test_one_way.py` AST-scans this file too.
"""

from __future__ import annotations

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

STATUS_LINE = (
    "承認音声リーダーは常駐で動作しています。"
    "承認待ちが発生すると自動で読み上げます。返事は端末でお願いします。"
)


class ApprovalVoiceStatus(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None

    # Do not change following tag of register capability
    # {{register capability}}

    async def announce_status(self) -> None:
        await self.capability_worker.speak(STATUS_LINE)
        # Return to the normal conversation flow; never enter an input/confirm loop.
        self.capability_worker.resume_normal_flow()

    def call(self, worker: AgentWorker):
        self.worker = worker
        self.capability_worker = CapabilityWorker(self.worker)
        self.worker.editor_logging_handler.info(
            "[ApprovalVoice] main.py ACTIVE — speaking status, no input capture"
        )
        self.worker.session_tasks.create(self.announce_status())
