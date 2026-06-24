"""approval-voice — interactive entry (M3.1, sandbox compliant).

The real readout work happens in the always-on daemon (background.py). This
`main.py` exists to satisfy the OpenHome interactive-ability convention and to
give a human an **SSH-free smoke trigger**: when triggered it (1) writes the
canonical 4-gate sample queue into persistent storage via
`capability_worker.write_file(...)` and resets the read-cursor, then (2) speaks a
status line. The background daemon then picks the queue up on its next poll and
reads all four gates aloud — so an on-device smoke needs no `seed_queue.py` /
SSH, only triggering this ability (design.md §M3.1; brief: "seed 相当は main.py の
write_file").

ONE-WAY GUARANTEE (design.md §3.1): output-only. It NEVER captures user input —
`tests/test_one_way.py` AST-scans this file.

M3.1 sandbox compliance: low-level platform access, module-scope data-encoding
imports and raw file access are rejected by the add-capability scan. So the seed
goes through the storage-name-based `write_file`, data encoding is imported inside
a method body, and the pure-logic `approval_voice` package is resolved by relative
import (same rationale as background.py; design.md §M3.1).
"""

from __future__ import annotations

from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

from .approval_voice.bridge import notifications_to_payload
from .approval_voice.storage import QUEUE_STORE, SEEN_STORE

STATUS_LINE = (
    "承認音声リーダーは常駐で動作しています。"
    "サンプルの承認待ちをキューに入れました。まもなく順に読み上げます。"
    "返事は端末でお願いします。"
)

# On-device smoke の seed データ。examples/announce_queue.json をミラーした
# 4 ゲート（worker_complete / ci_merge / escalation / reply_relay）1 件ずつ。
# notifications_to_payload を通すので §1.3 whitelist + gate 検証が seed にも効く。
SEED_NOTIFICATIONS = [
    {
        "id": "q-0001",
        "gate": "worker_complete",
        "title": "ワーカー完了の承認待ち",
        "question": "ワーカーが作業完了を報告しました。承認しますか。",
        "subject": "ログイン画面のリファクタリング",
        "options": ["承認", "差し戻し"],
        "created_at": "2026-05-31T10:00:00Z",
    },
    {
        "id": "q-0002",
        "gate": "ci_merge",
        "title": "マージ承認待ち",
        "question": "CI がグリーンになりました。マージしてよいですか。",
        "subject": "決済モジュールの改修",
        "options": ["マージ", "保留"],
        "created_at": "2026-05-31T10:05:00Z",
    },
    {
        "id": "q-0003",
        "gate": "escalation",
        "title": "エスカレーション",
        "question": "外部 API の仕様変更にどう追随するか方針を決めたい",
        "subject": "通知基盤の刷新",
        "options": ["方針A で進める", "方針B で進める", "保留して再検討"],
        "created_at": "2026-05-31T10:10:00Z",
    },
    {
        "id": "q-0004",
        "gate": "reply_relay",
        "title": "返答転送待ち",
        "question": "デザインレビューの指摘点を確認してほしい",
        "subject": "デザイナー",
        "options": [],
        "created_at": "2026-05-31T10:15:00Z",
    },
]


class ApprovalVoiceStatus(MatchingCapability):
    worker: AgentWorker = None
    capability_worker: CapabilityWorker = None

    # Do not change following tag of register capability
    # {{register capability}}

    async def _seed_queue(self) -> None:
        """Write the sample queue + reset the read-cursor for a fresh readout.

        Resetting `SEEN_STORE` lets a human re-trigger this ability and hear the
        full 4-gate readout again (the daemon would otherwise dedup already-spoken
        ids) — all without SSH access to the device.
        """
        # method-local import: a module-scope encode import is banned by the sandbox.
        import json

        if await self.capability_worker.check_if_file_exists(SEEN_STORE, False):
            await self.capability_worker.delete_file(SEEN_STORE, False)
        payload = notifications_to_payload(SEED_NOTIFICATIONS)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if await self.capability_worker.check_if_file_exists(QUEUE_STORE, False):
            await self.capability_worker.delete_file(QUEUE_STORE, False)
        await self.capability_worker.write_file(QUEUE_STORE, text, False)

    async def announce_status(self) -> None:
        await self._seed_queue()
        await self.capability_worker.speak(STATUS_LINE)
        # Return to the normal conversation flow; never enter an input/confirm loop.
        self.capability_worker.resume_normal_flow()

    def call(self, worker: AgentWorker):
        self.worker = worker
        self.capability_worker = CapabilityWorker(self.worker)
        self.worker.editor_logging_handler.info(
            "[ApprovalVoice] main.py ACTIVE — seeding sample queue, speaking status"
        )
        self.worker.session_tasks.create(self.announce_status())
