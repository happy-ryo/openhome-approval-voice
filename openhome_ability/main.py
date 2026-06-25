"""approval-voice — interactive entry (M3.1, sandbox compliant).

The real readout work happens in the always-on daemon (background.py), which
**self-seeds** the smoke sample and reads it aloud automatically (a
background_daemon has no trigger words). This `main.py` exists to satisfy the
OpenHome "Interactive + Daemon" convention (the canonical pattern, as in
community/alarm-timer) and as a required bundle file. If the runtime exposes its
trigger words, invoking it just speaks one status line confirming the reader is
installed — the on-device smoke does NOT depend on this being triggered
(design.md §M3.1-sandbox.6).

ONE-WAY GUARANTEE (design.md §3.1): output-only. It NEVER captures user input —
`tests/test_one_way.py` AST-scans this file.

M3.1 sandbox compliance: the pure-logic `approval_voice` package is resolved by
relative import; no low-level platform access, no module-scope data-encoding
import, no raw file access (design.md §M3.1-sandbox).
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
