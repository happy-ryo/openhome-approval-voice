# openhome-approval-voice

Secretary が判断仰ぎ/承認待ち(`awaiting_user`)で停止した瞬間、OpenHome が質問と選択肢を音声で読み上げる
**一方向**連携（音声返答キャプチャなし — 返事は端末から）。claude-org × OpenHome 連携チャレンジ。

ロードマップ: **M1 設計** → **M2 PoC（モック）** → **M3 実 OpenHome 接続 ← 現在**。
設計の詳細は `docs/design.md`（M1 + M3 実測で確定）を参照。

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
| `run_demo.py` | エンドツーエンド デモ（モック）。 |
| `openhome_ability/background.py` | **M3 実機**: 常駐 Background Ability。キューを `open()` で polling → 逐語 `speak()`。 |
| `openhome_ability/main.py` | **M3 実機**: 一方向の状態通知エントリ（入力取得なし）。 |

> M2 のモック（`approval_voice/ability.py`・`speak.py`・`run_demo.py`）は**単体テスト用に保持**。
> M3 の実読み上げは `openhome_ability/`（実 OpenHome SDK 形）。

## M3: 実 OpenHome 接続（DevKit on-device 逐語）

実測で確定した経路は **(C) DevKit on-device で `speak()` 直接 TTS による逐語読み上げ**。
cloud WebSocket は送信 text を LLM が**言い換える**ため承認用途に不適で不採用（`docs/design.md` §M3）。

### 前提ハード・電源・ネットワーク要件（Refs #7・公式 doc 出典付き）

事実は出典 URL 併記、未確認は ≈要検証。詳細・根拠は `docs/design.md` §M3.6 / §M3.3.1。

| 項目 | 要件 | 出典 / 注 |
|------|------|-----------|
| ハード | **Raspberry Pi Zero 2 W or higher** に OpenHome DevKit OS を flash（専用機ではない） | [blog](https://openhome.com/blog/ai-raspberry-pi-support) / [Devkit Setup Guide](https://docs.openhome.com/devkit_setup_guide)。Pi 4/5 対応は ≈要検証 |
| **① 電源** | **2A 以上の micro-USB チャージャー**。推奨は公式 **Raspberry Pi 12.5W Micro USB PSU = 5.1V/2.5A**。**自前 AC アダプタで独立給電し、PC からの給電に依存しない** | [Setup Guide](https://docs.openhome.com/devkit_setup_guide)（"2Amp minimum charger"）/ [RPi 12.5W PSU brief](https://datasheets.raspberrypi.com/power-supply/micro-usb-power-supply-product-brief.pdf)。Pi 4/5 は USB-C で別仕様(≈) |
| インターネット | **必須**。初回に DevKit が AP `Openhome_MACADDRESS` を立て、Wi-Fi 設定 + OpenHome アカウントでログイン。LLM はクラウド | [Setup Guide](https://docs.openhome.com/devkit_setup_guide) |
| **② PC 接続** | **運用には不要**。PC は **SD カードへの flash 時のみ**（Raspberry Pi Imager）。flash 後は iOS アプリ/OpenHome Client + Wi-Fi で設定・操作 | [Setup Guide](https://docs.openhome.com/devkit_setup_guide) / [blog](https://openhome.com/blog/ai-raspberry-pi-support) |
| 音声 I/O | **USB マイク**（default input `analog-mono`）＋ **Bluetooth スピーカー**（`a2dp-sink`） | [Setup Guide](https://docs.openhome.com/devkit_setup_guide) |

> 依頼者構成（PC=有線LAN / DevKit=Wi-Fi / 同一ルーター＝同一LAN）での **③ 本番 transport（PC→DevKit）**:
> 組織イベントは **PC 上で発生**するため exporter は PC 側に置く必要がある（DevKit に同居できない）。
> **推奨は HTTP pull**: PC 側 exporter がキュー JSON を atomic 書き込み → 最小 HTTP サーバで LAN 配信、
> DevKit の ability が poll 毎に **GET のみ**で取得（書き戻さない＝一方向維持）。
> DevKit 側に SSH/共有マウント等の受信サービスを足さずに済むのが決め手。
> 代替は scp/rsync push・共有マウント。**ability の outbound 通信可否は ≈要検証**（実機確認の最初の項目）。
> 詳細・代替比較・出典は `docs/design.md` §M3.3.1。

### 鍵の扱い（厳守）

- `OPENHOME_API_KEY` は**環境変数からのみ**。コード/`.env`/設定/コミット/ログに**絶対残さない**（public リポ）。
- ライブ実行は「その呼び出しだけ環境変数を前置」:
  - bash: `OPENHOME_API_KEY=**** py -3 your_script.py`
  - pwsh: `$env:OPENHOME_API_KEY="****"; py -3 your_script.py`
- `npx openhome-cli` は鍵を**ローカル disk に永続化**する。**依頼者の手元マシンでのみ**使用し、本リポには持ち込まない。

### デプロイ手順（依頼者が手元 DevKit で実施）

1. **DevKit を用意**: 実機、または Raspberry Pi(Zero 2 W/Pi 4/Pi 5)に OpenHome OS を flash。
   iOS アプリ もしくは OpenHome Client でアカウント接続し鍵を端末同期。
2. **専用エージェントを作成**（Dashboard の Quick Creation）。raw_prompt は逐語固定の例:
   「ユーザの発話を一字一句そのまま読み上げる。要約・言い換え・追加発言はしない。」
   （※ on-device の `speak()` は直接 TTS なので逐語性は保証されるが、会話フォールバック時の保険）。
   既存エージェントには影響させない。
3. **ability をバンドル**: `openhome_ability/` に純ロジック `approval_voice/`（`renderer/schema/poller/bridge`）を
   同梱（`background.py` が import する単一の真実源。重複実装は作らない）。
4. **アップロード**: `npx openhome-cli` の deploy、または
   `curl -X POST https://app.openhome.com/api/capabilities/add-capability/ -H "X-API-KEY: ****" -F "name=approval-voice" -F "category=background" -F "description=..." -F "trigger_words=承認読み上げ, approval voice" -F "zip_file=@./openhome_ability.zip"`
   （`name`/`category`/`description`/`trigger_words`/`zip_file` は必須）。
   対象エージェントに install/enable。
5. **キューのパスを設定**（任意・既定は `~/.openhome/approval_voice/`）:
   `APPROVAL_VOICE_QUEUE` / `APPROVAL_VOICE_SEEN` / `APPROVAL_VOICE_POLL_SECONDS`。
6. **bridge(exporter) を PC 側で起動**して `awaiting_user` 通知をキュー JSON に atomic 書き出し、
   同一 LAN へ配送（推奨 HTTP pull、上表/§M3.3.1）。DevKit の ability がそれを取得する。
   ※ 旧版は「bridge を DevKit に同居」と記したが、組織イベントは PC 上で発生するため exporter は PC 側（Refs #7 で訂正）。
7. **end-to-end 確認**: ゲート発火 → PC 側 exporter がキューを atomic 書き込み → DevKit の ability が取得・検知 →
   `send_interrupt_signal()` → **逐語読み上げ**。
   - ✅ 成功条件: 文面が**そのまま**（言い換えなし）聞こえる／自発話が**再転写されない**。
   - 失敗（import エラー/パス不一致/無音 等）は窓口へ報告（コードのデプロイ形を修正します）。

## デプロイパッケージ（DevKit 実機スモーク）

DevKit 上で ability を動かし、サンプルキューを投入して逐語読み上げを確認する
turnkey キットを `deploy/` に用意している。詳細は **[`deploy/DEPLOY.md`](deploy/DEPLOY.md)**。

```bash
# 1) デプロイ zip を生成（openhome_ability/ を root、approval_voice/ をその直下に同梱
#    = 単一の真実源）。生成後、展開→別プロセスで実機 ability の import を自動検証
#    （src.* はスタブ、approval_voice は本物 → import/パス不一致をビルド時に検出）
py -3 deploy/build_zip.py            # → dist/approval-voice-ability.zip

# 2) アップロード（REST。鍵は伏字プレースホルダ。design.md §M3.4 の契約）
curl -sS -X POST "https://app.openhome.com/api/capabilities/add-capability/" \
  -H "X-API-KEY: $OPENHOME_API_KEY" \
  -F "name=approval-voice" -F "category=background" \
  -F "description=承認待ちを逐語で読み上げる一方向 ability" \
  -F "trigger_words=承認読み上げ, approval voice" \
  -F "zip_file=@dist/approval-voice-ability.zip"

# 3) DevKit でサンプルキュー（4 ゲート）を配置（QUEUE は単一ファイルパス）
APPROVAL_VOICE_QUEUE=/data/approval_voice/announce_queue.json py -3 deploy/seed_queue.py
```

- アップロードは `npx openhome-cli` でも可（`deploy/DEPLOY.md` §2.1）。
- agent への install/enable と agent id の調べ方（get-all agents）も DEPLOY.md に記載。
- OpenHome がラップフォルダ付き zip を要求する場合は `--root-folder approval-voice`。
- 実機操作・ライブアカウントへの upload は依頼者が実施する。

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
