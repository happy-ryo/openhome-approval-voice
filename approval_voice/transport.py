"""On-device storage keys + the canonical smoke sample (single source).

The OpenHome on-device file storage is addressed by **name**, not by a device
filesystem path, so the queue and read-cursor are referenced by the stable keys
below (shared by the interactive seeder and the always-on watcher). Pure data /
constants only — bundle-safe.

`SAMPLE_NOTIFICATIONS` is the canonical 4-gate smoke sample (one per Secretary
gate). It is kept identical to `examples/announce_queue.json` by
`tests/test_pipeline.py::test_sample_matches_example`, so the sample has a single
source of truth across the PC tools and the on-device seeder.
"""

from __future__ import annotations

# capability_worker file-storage keys (names, not filesystem paths).
QUEUE_NAME = "approval_voice_queue.json"
SEEN_NAME = "approval_voice_seen.json"

# Watcher poll interval (seconds). Constant — the bundle cannot read env vars.
POLL_SECONDS = 15.0

# Canonical smoke sample: the four awaiting_user gates, one each.
SAMPLE_NOTIFICATIONS = [
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
