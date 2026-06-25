"""Smoke-test seed data for the on-device daemon (M3.1).

A background_daemon ability starts automatically on session and has **no trigger
words** (OpenHome docs: "Starts automatically on session", "No hotword trigger
needed") — so the Dashboard correctly shows "No triggers". That also means the
interactive `main.py` is never voice-invoked under category=background_daemon, so
it cannot be relied on to seed the queue (docs: "The JSON file may not exist yet
if main.py hasn't been triggered").

To make the on-device smoke self-contained, the daemon seeds this canonical
4-gate sample into its own storage on startup when `SMOKE_AUTOSEED` is true, then
reads it aloud verbatim. This proves the readout path on real hardware without any
trigger or SSH (design.md §M3.1-sandbox).

> Issue #7 (production): set `SMOKE_AUTOSEED = False` (one line) so the daemon
> stops injecting sample data and reads only what a real org-state exporter has
> written into its queue storage. The seed lives here, isolated, so flipping it
> off / removing it is a single, obvious change.

This is approval-voice app data (mirrors examples/announce_queue.json), the same
app-specific role storage.py / speak.py play — not shared sister-project logic.
It is pure data (no file I/O, no encoding), so it stays sandbox-clean.
"""

# Smoke autoseed switch. True for the M3.1 on-device smoke; Issue #7 sets False.
SMOKE_AUTOSEED = True

# Canonical 4-gate sample (worker_complete / ci_merge / escalation / reply_relay),
# 1 each, mirroring examples/announce_queue.json. Run through the bridge's
# public-hygiene + gate validation before it reaches storage.
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
