# openhome-approval-voice

Secretary が判断仰ぎ/承認待ち(`awaiting_user`)で停止した瞬間、OpenHome が質問と選択肢を音声で読み上げる
**一方向**連携（音声返答キャプチャなし — 返事は端末から）。claude-org × OpenHome 連携チャレンジ。

ロードマップ: **M1 設計** → **M2 PoC（モック）← 現在** → M3 実接続。
設計の詳細は `docs/design.md`（M1 成果物）を参照。

## M2 のスコープ

ローカル完結の動く scaffold。OpenHome API はすべてモック。次のデータパスを検証する:

```
awaiting_user 通知 → ブリッジ(read-only) → 共有キューJSON → 既読カーソル(dedup) → 4ゲート文面生成 → speak()モック → stdout/ログ
```

- 実 OpenHome には繋がない（M3 で接続）。
- 音声**入力** API（`user_response` / `run_io_loop` / `run_confirmation_loop` / `start_audio_recording`）は呼ばない。
  これは `tests/test_one_way.py` が AST 走査で構造的に担保する（design.md §3.1）。

## 構成

| ファイル | 役割 |
|----------|------|
| `approval_voice/schema.py` | 共有 announce-item スキーマ（design.md §1.3）。姉妹 ambient-announcer との**共通契約**。 |
| `approval_voice/bridge.py` | read-only ブリッジ。`awaiting_user` 通知 → §1.3 アイテム。public 衛生フィルタの一元適用点。 |
| `approval_voice/renderer.py` | 4 ゲート（worker完了 / CI green マージ / エスカレーション / 返答転送）の読み上げ文面ジェネレータ（§2）。 |
| `approval_voice/poller.py` | 既読カーソル + dedup（§3.2 / §5-3）。二重読み上げ・取りこぼし防止。 |
| `approval_voice/ability.py` | Background Ability skeleton（§5-2）。poll → render → speak。 |
| `approval_voice/speak.py` | OpenHome `speak()` のモック。発話文字列を stdout/ログへ。 |
| `examples/announce_queue.json` | 手書きモックキュー（4 ゲート 1 件ずつ）。 |
| `run_demo.py` | エンドツーエンド デモ。 |

## 実行方法（Windows）

```powershell
# 依存（pytest のみ）
py -3 -m pip install -e ".[dev]"

# 単体テスト（4 ゲートの文面固定 + 一方向担保 + データパス）
py -3 -m pytest -q

# エンドツーエンド デモ（モックキューを読み上げ → stdout）
py -3 run_demo.py
```

> Windows コンソールは既定 cp932 のため、`run_demo.py` は冒頭で stdout を utf-8 に再設定する。

## 一方向性の担保（design.md §3）

返答経路を「実装しない」のではなく**構造的に持たせない**:

- 出力は `speak()` モックのみ。音声入力 API は一切呼ばない（`test_one_way.py` が強制）。
- ブリッジは read-only。組織状態への書き戻し経路を持たない。
- 既読管理は OpenHome 側ローカルで完結し、組織状態に副作用を与えない。

## 共有コンポーネント契約（発散防止）

姉妹 **openhome-ambient-announcer** と同一の中核機構（state→JSON ブリッジ / Ability skeleton / polling 基盤）。
将来の共通ライブラリ `openhome-org-voice-core` を見据え、`schema.py` の §1.3 フォーマットを**両者共通の契約**として固定する。
選択肢を持たないケースは別スキーマに分岐させず `options` の空配列で吸収する（design.md §5）。
