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
| `approval_voice/storage.py` | OpenHome 永続ストレージ名（キュー / 既読カーソル）+ poll 間隔の定数。単一の真実源。 |
| `approval_voice/sample.py` | スモーク用 4 ゲート seed データ + `SMOKE_AUTOSEED` フラグ。Issue #7 で False。 |
| `openhome_ability/background.py` | **M3.1 実機**: 常駐 Background Daemon。起動時に self-seed（スモーク）→ `capability_worker` storage API で polling → 逐語 `speak()`（sandbox 準拠・trigger 不要で自動起動）。 |
| `openhome_ability/main.py` | **M3.1 実機**: interactive エントリ（status 読み上げのみ・入力取得なし）。必須ファイル + 導通確認。スモークは main.py に非依存。 |

> M2 のモック（`approval_voice/ability.py`・`speak.py`・`run_demo.py`）は**単体テスト用に保持**。
> M3 の実読み上げは `openhome_ability/`（実 OpenHome SDK 形, M3.1 で add-capability sandbox 準拠）。

> **M3.1 sandbox 準拠（Refs #11）**: OpenHome の add-capability 静的スキャンは
> 低レベル OS アクセス・module 直下の encode import・生のファイルオープン・低レベル signal を
> 弾く。よって ability は file path/生 open() をやめ **storage-name ベースの非同期
> `capability_worker` API**（`read_file`/`write_file`/`check_if_file_exists`/`delete_file`,
> 第2引数 False=永続）を使い、純ロジック `approval_voice` を **相対 import**
> （`from .approval_voice...`、`sys.path` 撤去）で解決する。zip は**ラップフォルダ必須**。
> 詳細は `docs/design.md` §M3.1-sandbox。

## M3: 実 OpenHome 接続（`speak()` 逐語・実行は cloud / DevKit は I/O 端末）

実測で確定した経路は **(C) `speak()` 直接 TTS による逐語読み上げ**。
cloud WebSocket は送信 text を LLM が**言い換える**ため承認用途に不適で不採用（`docs/design.md` §M3）。

> **アーキテクチャ要点（2026-06-26 live 統合で確定）**: **ability コードは OpenHome の
> cloud（Ubuntu サーバ）上で実行**され、**DevKit はマイク/スピーカの audio I/O 端末**として
> cloud の `speak()` 出力を鳴らすだけ（DevKit 上でコードは走らない）。本書で多用する
> **"on-device 逐語" / "DevKit on-device"** は**実行場所**ではなく **`speak()` 経由＝LLM を
> 介さず逐語**という**経路**の意味で読む。根拠は cloud error log の path
> `/home/ubuntu/.../user_capabilities/<user_id>/...`（user 毎の多重ホスティング）。
> この帰結として ability の outbound pull は **cloud から発する**ため、配信元 PC は
> **public HTTPS（例: cloudflared tunnel）で到達可能**でなければならず、**private LAN IP
> （192.168.x.x）は cloud から不可達**（`deploy/DEPLOY.md` §4.3(A)/§6・`docs/design.md` §M3.0）。

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
> **primary は requests HTTP pull**: PC 側 exporter がキュー JSON を atomic 書き込み → public
> HTTPS（cloudflared tunnel 等）で配信、**cloud 上の ability** が poll 毎に **GET のみ**で取得
> （書き戻さない＝一方向維持）。`requests` は add-capability で受理（実測 201）、
> `urllib`/`http.client`/`socket` は forbidden import で reject。
> **2026-06-26 の live 統合で pull egress は確定**（cloud→public HTTPS で GET 成功・読み上げ確認）。
> ⚠️ ability は cloud 実行ゆえ **private LAN IP（192.168.x.x）は不可達**で、配信元 PC を
> **public HTTPS で公開**する必要がある（日常運用は `deploy/DEPLOY.md` §6 の Startup Runbook）。
> **fallback の PC-side push（scp/sftp）は cloud 実行の判明で前提が崩れた**（push 先は DevKit だが
> storage は cloud 側にあり scp が届かない）— コードは残置・参考扱い（`deploy/DEPLOY.md` §4.3(B)）。
> 詳細・出典は `deploy/DEPLOY.md` §4.3 / §6 / `docs/design.md` §M3.0 / §M3.3.1。

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
3. **ability zip をビルド**: `py -3 deploy/build_zip.py` → `dist/approval-voice-ability.zip`。
   `openhome_ability/`（main/background/__init__/requirements）をラップフォルダ `approvalvoice/`
   直下へ、純ロジック `approval_voice/` をそのさらに下へ同梱（`background.py` が**相対 import**
   する単一の真実源）。ビルド時に **sandbox lint** + 展開→パッケージ import 検証が走る。
4. **アップロード**: `npx openhome-cli` の deploy、または
   `curl -X POST https://app.openhome.com/api/capabilities/add-capability/ -H "X-API-KEY: ****" -F "name=approvalvoice" -F "category=background_daemon" -F "description=..." -F "trigger_words=承認読み上げ, approval voice" -F "zip_file=@dist/approval-voice-ability.zip"`
   （`name`/`category`/`description`/`trigger_words`/`zip_file` は必須。**`category=background_daemon`**、
   `name` は英数字のみ）。対象エージェントに install/enable。
5. **enable → セッション開始だけ（seed もトリガも不要）**: background_daemon は
   **セッション開始時に自動起動し trigger を持たない**（Dashboard の "No triggers for this ability" は
   **正常表示**）。`background.py` が起動時に `SMOKE_AUTOSEED=True` のときサンプル 4 ゲートを自 storage に
   **self-seed** + 既読カーソル reset し、次の poll で逐語読み上げする。storage 名は固定
   （`announce_queue.json` / `announce_seen.json`）。再テストはセッション再起動だけ。
6. **end-to-end 確認**: capability を agent に install/enable → エージェントとのセッションを開始 →
   daemon 自動起動 → self-seed → `send_interrupt_signal()` → **4 ゲートを逐語読み上げ**。
   - ✅ 成功条件: 文面が**そのまま**（言い換えなし）聞こえる／自発話が**再転写されない**。
   - 失敗時（無音/起動しない 等）: daemon は各ステップを `[ApprovalVoice]` prefix で
     **"Open In Editor" → log タブ**に出力する。期待ログ列と「どこで止まったか→原因」早見表は
     `deploy/DEPLOY.md` §5.5。`speak ... done` まで出て無音なら audio device（§5.5.1）。
   - 本番（Issue #7）は `approval_voice/sample.py` の `SMOKE_AUTOSEED=False` で self-seed を止め、
     real exporter データのみ読む。
   > 本スモークは daemon の self-seed（`SMOKE_AUTOSEED`）で端末内完結する。本番（live org state →
   > 端末）の PC→DevKit 配送は storage-name モデルで別問題として残る（**Issue #7 の確定事項**、
   > `docs/design.md` §M3.1-sandbox.5/.6）。
   > 本スモークは `main.py` seed で端末内完結する。

## デプロイパッケージ（DevKit 実機スモーク）

DevKit 上で ability を動かし、サンプルキューを投入して逐語読み上げを確認する
turnkey キットを `deploy/` に用意している。詳細は **[`deploy/DEPLOY.md`](deploy/DEPLOY.md)**。

```bash
# 1) デプロイ zip を生成（ラップフォルダ approvalvoice/ 直下に openhome_ability、その下に
#    approval_voice を同梱 = 単一の真実源）。生成時に sandbox lint（add-capability 禁止
#    パターン走査）→ 展開→別プロセスで <wrap>.background / <wrap>.main をパッケージ import 検証
#    （src.* はスタブ、approval_voice は本物 → 相対 import/パス不一致をビルド時に検出）
py -3 deploy/build_zip.py            # → dist/approval-voice-ability.zip（既定でラップフォルダ付き）

# 2) アップロード（REST。鍵は伏字プレースホルダ。design.md §M3.4 の契約）
curl -sS -X POST "https://app.openhome.com/api/capabilities/add-capability/" \
  -H "X-API-KEY: $OPENHOME_API_KEY" \
  -F "name=approvalvoice" -F "category=background_daemon" \
  -F "description=承認待ちを逐語で読み上げる一方向 ability" \
  -F "trigger_words=承認読み上げ, approval voice" \
  -F "zip_file=@dist/approval-voice-ability.zip"

# 3) agent に install/enable → セッション開始だけ（SSH も trigger も不要）。
#    background_daemon は自動起動し、daemon が起動時に self-seed（SMOKE_AUTOSEED）で
#    サンプル 4 ゲートを storage に書き込み → 逐語読み上げ。"No triggers" 表示は正常。
#    （旧 deploy/seed_queue.py は storage-name モデルでは無効化のため削除）
```

- アップロードは `npx openhome-cli` でも可（`deploy/DEPLOY.md` §2.1）。
- agent への install/enable と agent id の調べ方（get-all agents）も DEPLOY.md に記載。
- `category` は **`background_daemon`**（CLI の `VALID_CATEGORIES` で確定。`background` は無効値）。
- ラップフォルダ名を変えたい場合は `--root-folder <name>`（相対 import は名前非依存）。
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
