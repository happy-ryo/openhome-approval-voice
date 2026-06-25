# openhome-approval-voice — 設計ドキュメント (M1)

> Secretary（人間窓口）が判断仰ぎ／承認待ち（`awaiting_user`）で停止した瞬間、
> OpenHome が「質問と選択肢」を音声で**読み上げるだけ**の一方向連携。
> 音声での返答キャプチャは行わない（返事は従来どおり端末から）。

- **チャレンジ**: claude-org × OpenHome DevKit 連携
- **本ドキュメントの位置づけ**: M1（設計・技術調査）。M2 = PoC（モック）、M3 = 実接続。
- **凡例**: ✓ = 既存リサーチで公式 doc 確認済み / ≈ = 要検証（M2/M3 で確定）。
  OpenHome 基礎調査は社内の既存リサーチ（OpenHome 連携検討資料, 2026-05-31）を再利用し、
  そこで ≈ とフラグされた点のみ本件向けに絞り込む。

---

## 0. スコープと非ゴール

### ゴール（このプロジェクトがやること）
- 窓口が **`awaiting_user` ゲート**（後述の 4 種）で停止した「待ち」を検知する。
- その「質問文＋選択肢」を OpenHome Agent が**能動的に読み上げる**（hotword 不要）。
- 画面を見ていなくても「いま何の承認待ちで止まっているか」が耳で分かる状態にする。

### 非ゴール（明示的にやらないこと）
- ❌ 音声での返答キャプチャ（`user_response()` / `run_confirmation_loop()` を使わない）。
- ❌ 組織状態の変更・コマンド実行（読み取り専用。承認/却下を音声から発行しない）。
- ❌ 双方向の意思決定ループ。**返答は引き続き端末側のキーボード操作で行う**。

> 本件は、既存リサーチの **A-5「承認ゲートの音声化（双方向）」から
> 音声入力経路を取り除いた "読み上げ専用サブセット"** である。
> 双方向化（音声で承認/却下を返す）は本プロジェクトの範囲外であり、
> 将来別プロジェクトとして検討する（§3 参照）。

---

## 1. アーキテクチャ

### 1.1 データフロー（一方向）

```
 ┌─────────────────────┐   ① 停止イベント        ┌──────────────────────┐
 │  組織（窓口/Secretary）│  awaiting_user ゲートで │  ローカルブリッジ        │
 │  ・worker完了承認      │  「保留中の決定」が発生  │  (state→JSON エクスポータ)│
 │  ・CI green マージ承認 │ ─────────────────────▶ │  ・保留項目を読み取り     │
 │  ・エスカレーション    │                         │  ・読み上げ用 JSON に整形 │
 │  ・返答転送           │                         │  ・キューファイルへ追記   │
 └─────────────────────┘                         └───────────┬──────────┘
                                                              │ ② 共有 JSON
                                                              │   (announce queue)
                                                              ▼
 ┌─────────────────────┐   ④ speak()             ┌──────────────────────┐
 │   🔊 読み上げ          │ ◀───────────────────── │  OpenHome             │
 │  「質問＋選択肢」      │   send_interrupt_signal │  Background Ability   │
 │   を音声出力          │   → speak()             │  (background.py)       │
 └─────────────────────┘                         │  ・while + sleep で    │
                                                  │    ③ キューを polling   │
   ※ 戻り経路なし。返答は端末から。                │  ・差分(未読)を検知     │
                                                  └──────────────────────┘
```

### 1.2 各段の責務

| 段 | コンポーネント | 責務 | 種別 |
|----|----------------|------|------|
| ① | 組織（窓口） | `awaiting_user` ゲートで停止し「保留中の決定（質問・選択肢・対象）」が発生 | 既存 |
| ② | ローカルブリッジ（state→JSON エクスポータ） | 組織側の保留状態を**読み取り専用**で取得し、読み上げ用 JSON に整形してキューへ追記 | 新規（姉妹と共有） |
| ③ | OpenHome Background Ability | `while True` + `session_tasks.sleep()` でキューを polling、未読項目の差分を検知 ✓ | 新規（姉妹と共有） |
| ④ | OpenHome 音声出力 | `send_interrupt_signal()` で割り込み → `speak()` で読み上げ ✓ | OpenHome SDK |

- **戻り経路を持たない**のが本件の設計上の要（§3）。④ の後に音声入力を受ける段は存在しない。
- ② のブリッジは組織状態を**変更しない**。あくまで「保留中の決定」をスナップショットして
  読み上げ用フォーマットに変換するだけ（read-only export）。
- ③④ は OpenHome の **Background（Always-On）Ability** パターンそのもの ✓。
  hotword 不要・スリープ中も動作し、条件成立時に能動発話できる。

### 1.3 共有 JSON（announce queue）の契約（概念）

組織内部の状態スキーマを生写しせず、**ブリッジが出力する独自フォーマット**を定義する。
1 件 = 1 つの「読み上げるべき待ち」。例（フィールドは概念レベル）:

```jsonc
{
  "id": "stable-unique-id",        // 既読管理・重複排除の鍵
  "gate": "worker_complete",        // worker_complete | ci_merge | escalation | reply_relay
  "title": "短い見出し",            // 読み上げ冒頭の一文
  "question": "何を待っているか",   // 本文（質問）
  "options": ["承認", "却下"],      // 選択肢（無い場合は空配列）
  "subject": "対象の概念ラベル",    // 例: タスク名/PR の概念識別子（内部 ID は含めない）
  "created_at": "ISO8601"
}
```

> **public 衛生**: `gate` 値や `subject` は**概念ラベル**にとどめ、
> 組織内部の生 state スキーマ・内部識別子・マシン固有パス・内部フック名は載せない。

---

## 2. 対象ゲートと読み上げ文面テンプレート案

窓口が `awaiting_user` で停止する代表的な 4 ゲート。文面は「①どのゲートか ②対象 ③選択肢」を
**端末を見なくても再現できる粒度**で、かつ音声で聞き取りやすい短さに圧縮する。

> 設計指針:
> - 冒頭にゲート種別を必ず告げる（「承認待ちです」だけでは何の承認か分からない）。
> - 選択肢は番号付きで列挙し、**「返事は端末でお願いします」**で締める（一方向であることを明示）。
> - 復唱・確認は行わない（音声入力を受けないため）。

### 2-1. worker 完了承認（`worker_complete`）
> 「ワーカー完了の承認待ちです。**{タスクの概念ラベル}** が作業完了を報告しました。
> 承認すると次の工程へ進みます。選択肢は、1 承認、2 差し戻し。返事は端末でお願いします。」

### 2-2. CI green マージ承認（`ci_merge`）
> 「マージ承認待ちです。**{対象の概念ラベル}** の CI がグリーンになりました。
> マージしてよいか確認をお願いします。選択肢は、1 マージ、2 保留。返事は端末でお願いします。」

### 2-3. エスカレーション（`escalation`）
> 「エスカレーションです。**{対象の概念ラベル}** で判断を仰いでいます。
> 内容は『{質問の要約}』。選択肢は、{列挙}。返事は端末でお願いします。」

### 2-4. 返答転送（`reply_relay`）
> 「転送された返答待ちです。**{相手の概念ラベル}** から確認事項が届いています。
> 内容は『{質問の要約}』。あなたの返事を待っています。返事は端末でお願いします。」

各テンプレートは Ability 内に**文面ジェネレータ**として実装し、`gate` 値で分岐する。
長文になりがちな `question` は、必要なら `text_to_text_response()` で「一文要約」に圧縮してから
読み上げる（≈ 要検証: 声向けの冗長度調整）。

---

## 3. 「一方向のみ」を担保する設計

音声からの返答が誤って組織に届くと、承認の取り違え（誤承認/誤マージ）に直結する。
そのため**一方向性は "実装しないことで担保する"** のではなく、**経路を構造的に持たせない**。

### 3.1 OpenHome（Ability）側の制約
- 読み上げ Ability は **`speak()` / `text_to_speech()` のみ**を出力に使う。
- 次の API を**呼ばない**ことをコードレビュー観点として固定する:
  - ❌ `user_response()`（ユーザ発話の取得）
  - ❌ `run_io_loop()` / `run_confirmation_loop()`（対話・Yes/No 確定）
  - ❌ `start_audio_recording()`（録音）
- 読み上げ後は速やかに polling ループへ戻る（対話状態に入らない）。

### 3.2 ブリッジ側の制約
- ブリッジは組織状態を **read-only** で読むのみ。組織へ書き戻す送信経路
  （peer messaging への送信等）を**実装しない**。
- 共有 JSON は「組織 → OpenHome」の**単方向キュー**。逆向きのチャネルを作らない。
- 既読管理は OpenHome 側ローカルで完結させ、組織側の状態を更新しない
  （= 読み上げたことが組織に反映されない＝副作用ゼロ）。

### 3.3 返答経路
- 承認/却下/選択は**従来どおり端末**（窓口ペインのキーボード操作）で行う。
- OpenHome は「通知装置」に徹し、意思決定には一切関与しない。

### 3.4 A-5（双方向）との差分（将来の発散防止）
| 観点 | 本件（approval-voice） | A-5（双方向・将来別件） |
|------|------------------------|--------------------------|
| 音声入力 | なし | `user_response()`/`run_confirmation_loop()` |
| 組織への書き戻し | なし（read-only） | peer messaging で承認値を中継 |
| 共有 JSON | 単方向キュー | 双方向（保留＋回答） |
| リスク | 取り違えなし（通知のみ） | 復唱確認が必須 |

---

## 4. OpenHome 接続点と要検証 API

| 接続点 | API / 機構 | 状態 | 本件での用途 |
|--------|------------|------|--------------|
| 通知トリガー受け口 | Background Ability の `while True` + `session_tasks.sleep()` で共有ファイルを polling | ✓ 確認済み | キューの未読検知（主経路） |
| （代替）通知トリガー | WebSocket voice-stream で外部から push | ✓ 機構は確認 / ≈ 本用途は要検証 | polling より低レイテンシな代替 |
| 割り込み | `send_interrupt_signal()` | ✓ | 読み上げ前に現在の出力を中断 |
| 読み上げ | `speak()` / `text_to_speech(text, voice_id)` | ✓ | 質問＋選択肢の音声出力 |
| 要約整形 | `text_to_text_response(prompt, history, system)` | ✓ | 長い `question` を声向けに短縮 |
| 永続/セッションファイル | `read_file()` / `check_if_file_exists()` / `write_file()` | ✓ | 既読カーソルの保持 |
| Ability 雛形 | `MatchingCapability` 継承 + `call(worker)` + 登録マーカー + 終了時 `resume_normal_flow()` | ✓ | Ability の骨格 |

### 要検証ポイント（≈ / M2・M3 で確定）
1. **キュー検知のレイテンシ**: polling 間隔と「停止 → 読み上げ」までの体感遅延。
   許容できなければ WebSocket push へ切替（≈ 要検証）。
2. **共有ファイルの同時アクセス**: ブリッジ書き込みと Ability 読み取りの競合。
   追記専用 + 既読カーソル方式で回避する想定だが要検証（≈）。
3. **割り込みの作法**: 会話中／スリープ中それぞれで `send_interrupt_signal()` →
   `speak()` がどう振る舞うか（割り込みすぎないバッチ化の要否）（≈）。
4. **読み上げの可読性**: 選択肢列挙・要約の冗長度を声向けに最適化（≈）。
5. **ブリッジの常駐安定性**: 長時間稼働・再接続・トラストバウンダリ（API キー管理）（≈）。

> M2（モック PoC）では組織側を**手書きの共有 JSON**で置き換え、③④（Background Ability の
> polling → 読み上げ）を先に成立させる。M3 で ②（実ブリッジ＝実 state からの export）を接続する。

---

## 5. 姉妹プロジェクト openhome-ambient-announcer との共有コンポーネント

姉妹 **openhome-ambient-announcer** は本件と**同一の中核機構**を持つ:
「組織 state → ローカルブリッジ → OpenHome `speak()`」。
- **本件**: `awaiting_user` の**質問＋選択肢**を読み上げる（承認系・止まっている待ちが対象）。
- **姉妹**: 一般イベント（タスク完了・ブロッカー発生等）を**アナウンス**する（流れているイベントが対象）。

機構が同じである以上、**ブリッジ設計が両者で乖離しないこと**が最重要。
以下を共有コンポーネントとして切り出し、**将来の共通ライブラリ化**（仮称 `openhome-org-voice-core`）を見据える。

### 共有すべきコンポーネント
1. **state → JSON ブリッジ（エクスポータ基盤）**
   - 組織状態を read-only で読み、**共通の announce-item フォーマット**（§1.3）に整形して
     キューへ追記する基盤。`gate`/`kind` フィールドで本件・姉妹を区別。
   - public 衛生ルール（内部スキーマ・内部識別子・パスを出さない）を**ブリッジ層で一元適用**。
2. **Ability skeleton（雛形）**
   - `MatchingCapability` 継承 + 登録マーカー + Background ループ + `resume_normal_flow()` の定型骨格。
   - 「polling → 差分検知 → 読み上げ」までを共通化し、**文面ジェネレータだけを差し替える**。
3. **polling 基盤（共通ランタイム）**
   - `while True` + `session_tasks.sleep()` のループ、**既読カーソル管理・重複排除（dedup）**、
     `send_interrupt_signal()` の割り込みポリシー。
   - 本件と姉妹で**同一の既読管理ロジック**を使い、二重読み上げ/取りこぼしを防ぐ。

### 乖離防止の約束事（contract）
- 共有 JSON フォーマット（§1.3）を**両プロジェクト共通の契約**として固定する。
  本件は `options` を持つ（選択肢付き）、姉妹は持たない場合がある、等の差は
  **同一スキーマ内のオプショナルフィールド**で吸収し、別スキーマに分岐させない。
- ブリッジ／skeleton／poller は本件側で先に実装し、安定後に共通ライブラリへ抽出する
  （早すぎる抽象化を避け、2 利用者が揃った段階で公約数を切り出す）。

```
        openhome-org-voice-core (将来の共通ライブラリ)
        ├─ bridge/   state→JSON エクスポータ基盤 + public 衛生フィルタ
        ├─ ability/  Background Ability skeleton（雛形）
        └─ poller/   polling + 既読カーソル + dedup + 割り込みポリシー
              │
   ┌──────────┴───────────┐
   ▼                      ▼
 approval-voice        ambient-announcer
 （本件: 質問読み上げ）  （姉妹: イベント告知）
 文面ジェネレータのみ差替  文面ジェネレータのみ差替
```

---

## 6. 次アクション（M2 へ）

1. 共有 JSON フォーマット（§1.3）を姉妹プロジェクトと**合意・固定**する（乖離防止の起点）。
2. Background Ability の skeleton（§5-2）を作り、**手書きキュー JSON**で polling → 読み上げを通す（M2 モック）。
3. 4 ゲートの文面ジェネレータ（§2）を実装し、声向けの可読性を実測（≈ 要検証 4）。
4. 一方向性のコードレビュー観点（§3.1 の禁止 API リスト）を**雛形のチェックリストに固定**する。
5. polling レイテンシ・同時アクセス（§4 要検証 1・2）を計測し、必要なら WebSocket push を検討。

---

## M3. 実 OpenHome 接続（実測で確定）

M3 で実 OpenHome API に接続し、M1 で ≈（要検証）としていた点を実測で確定した。
**凡例更新**: 以下はすべて実 API / 実コード（openhome-dev/abilities の稼働中 ability）で確認済み。

### M3.0 経路の決定（cloud WS 不採用 → (C) DevKit on-device 逐語）

| 経路 | 実測した挙動 | 本件での可否 |
|------|--------------|--------------|
| Cloud WebSocket `wss://app.openhome.com/websocket/voice-stream/{KEY}/{AGENT_ID}` | 送信 text を**ユーザ発話(`type:transcribed`)として扱い**、エージェントの LLM が**応答を生成して読み上げる**。本質的に双方向チャネル | ❌ 承認文面が**言い換え**られ取り違えリスク。不採用 |
| REST API `app.openhome.com`（`X-API-KEY` ヘッダ） | 鍵で認証成功（HTTP 200）。エージェント一覧取得可。ability アップロード可 | 管理用途のみ（逐語読み上げ手段ではない） |
| **(C) DevKit on-device** Background Ability の `speak()` | `speak()` は**直接 TTS**＝**逐語**読み上げ。LLM を介さない | ✅ **採用**。approval-voice は承認文面の正確さが要 |

> 依頼者判断により (C) を採用。逐語性は `speak()` が直接 TTS する事実から来る（raw_prompt
> ではない）。専用エージェントは既存 "Dev Guide Ori" と分離し voice 設定を持たせるための器。

### M3.1 OpenHome SDK 表面（実コードで確定）

稼働中の background ability（`openhome-dev/abilities` · `community/alarm-timer/background.py`）
を一次情報として確認した（本件はコード実行不可のため doc 要約でなく実コードで裏取り）:

```python
from src.agent.capability import MatchingCapability
from src.agent.capability_worker import CapabilityWorker
from src.main import AgentWorker

class XxxWatcher(MatchingCapability):
    # Do not change following tag of register capability
    # {{register capability}}
    def call(self, worker: AgentWorker, background_daemon_mode: bool):
        self.capability_worker = CapabilityWorker(self.worker)   # ← self.worker（self ではない）
        self.worker.session_tasks.create(self.watch_loop())

    async def watch_loop(self):
        while True:
            ...                                        # ファイル検知
            await self.capability_worker.send_interrupt_signal()   # speak 前に 1 回だけ
            await self.capability_worker.speak(text)               # 逐語読み上げ
            await self.worker.session_tasks.sleep(15.0)            # asyncio.sleep は不可
```

| 接続点 | M1 表記 | M3 実測 |
|--------|---------|---------|
| Ability 雛形 | ✓ | ✓ 確定: `MatchingCapability` 継承 + `# {{register capability}}` マーカー + `call()` + `session_tasks.create()` |
| background `call()` 署名 | （未記載） | **`call(self, worker, background_daemon_mode: bool)`**（interactive の `call(self, worker)` と異なる） |
| `CapabilityWorker` 取得 | ≈ | **`CapabilityWorker(self.worker)`**（how-to-build doc の `CapabilityWorker(self)` は誤り） |
| 通知受け口（polling） | ≈ 要検証1 | ✓ `while True` + `await session_tasks.sleep(秒)`。`asyncio.sleep` 禁止。10–30 秒が標準 |
| 割り込み | ≈ 要検証3 | ✓ `await send_interrupt_signal()` を **speak の前に 1 回だけ**（ループ内で呼ばない） |
| 読み上げ | ✓ | ✓ `await speak(text)` = 直接 TTS（逐語） |
| ファイル協調 | ≈ 要検証2 | ✓ SDK は `read_file/write_file/check_if_file_exists/delete_file`（async, 第2引数 False）。Local Ability は素の `open()` も可（device の Linux パス） |

### M3.2 一方向性（実測で強化）

- 入力取得 API は実 SDK 上 `user_response()` と `run_io_loop()`（"speak してから返答を待つ"）の 2 つ。
  両方とも禁止集合（§3.1）に既に含まれ、**`tests/test_one_way.py` の AST 走査を
  `openhome_ability/` にも拡張**して on-device 経路を網羅した。
- background-abilities doc により、`send_interrupt_signal()` を speak 前に呼ばないと
  **デーモンの発話がユーザ入力として転写される**。割り込みは一方向担保の構造的ガードも兼ねる。
- 読み上げに LLM 整形（`text_to_text_response`）は使わない（逐語が要件。要検証4 は「逐語固定」で決着）。

### M3.3 transport の決定（要検証2 の決着・Q2）

> **M3.1 で更新（Refs #11）**: 本節の consumer 側「素の `open()` でファイルを読む」機構は
> add-capability sandbox スキャンで弾かれるため廃止し、`capability_worker` の storage-name
> ベース API（`read_file`/`write_file`/`check_if_file_exists`/`delete_file`）へ置換した。
> 詳細は §M3.1-sandbox。以下は当時の決定の記録。

**採用: 端末ローカルの固定 JSON ファイル**を bridge が**アトミック書き込み**(temp→`os.replace`)し、
ability が素の `open()` で**読み取り専用**ポーリング。既読カーソルは**別ファイル**に
ability 側ローカルで永続（組織へ書き戻さない＝副作用ゼロ, §3.2）。パス/間隔は環境変数で可変。

採用理由（最も単純かつ確実）:
1. Local Ability の文書化された FS パターン（device パスへの素の `open()`）にそのまま乗る。
2. ability 側（consumer）は**端末ローカルファイルの読み取りのみ**で完結し、ネットワーク I/O を持たない。
3. `os.replace` のアトミック rename で poller が**半端な書き込みを絶対に読まない**。
4. 既読カーソル dedup で二重読み上げ・取りこぼしを防止。

> 代替（同一 ability 内で writer/reader が完結する場合）は SDK の `read_file/write_file`
> が定石。本件は writer が外部 bridge のため、store 解決の曖昧さを避けて素の `open()` を採る。

> **訂正（Refs #7）**: 旧記述は「writer=bridge が同一端末に同居するためネットワーク不要」と
> していたが、これは**誤り**。bridge が読む組織の `awaiting_user` 状態は **Secretary（窓口）が
> 動く PC 上で発生**するため、状態を読む exporter は DevKit に同居できない。本番では
> PC→DevKit の経路が**必須**になる（下記 §M3.3.1）。「端末ローカルファイル + 素の `open()`」は
> あくまで ability（consumer）側の**読み取り機構**として有効で、その手前にどう配送するかは別問題。

### M3.3.1 同一 LAN での本番 transport（PC→DevKit, Refs #7）

依頼者の構成: **PC=有線 LAN / DevKit=Wi-Fi / 同一ルーター（＝同一 LAN・同一サブネット）**。
組織イベント（`awaiting_user`）は PC 上で発生し、読み上げる ability は DevKit 上で動くため、
bridge は機能上**2 つに割れる**:

| 役割 | 動作場所 | やること | 根拠 |
|------|----------|----------|------|
| **exporter（writer）** | **PC** | 組織状態を read-only で読み、§1.3 アイテムへ整形し、キュー JSON を**アトミック書き込み**(temp→`os.replace`)して LAN へ公開 | 組織状態が PC-local（事実） |
| **consumer（reader）** | **DevKit** | キューを取得 → dedup → 逐語 `render` → `speak()` | ability の読み取り機構は §M3.1 で確認済（事実） |

**推奨 transport（primary）: HTTP pull**
- PC 側 exporter がキュー JSON を**アトミック書き込み**したうえで、PC 上の**最小 HTTP サーバ**で
  静的配信する（例: `py -3 -m http.server` をキュー dir で、または極小 Flask）。
- DevKit 上の ability が poll 毎に **HTTP GET** で取得し、既存の dedup/render/`speak()` に流す。
- 同一サブネットのためレイテンシ・到達性は良好。サーバ停止/PC スリープ時は GET 失敗 →
  その tick はスキップして次回リトライ（クラッシュしない）。

**HTTP pull を推す理由**:
1. DevKit（appliance OS）側に **SSH/共有マウント等の追加サービスを一切立てない**。受信面を増やさない。
2. 「サーバ」負荷はすべて**我々が完全制御できる PC 側**に寄せられる。
3. 完全性（atomicity）は**自然に担保**: HTTP は本体を完結受信できなければ GET 自体が失敗するため、
   半端な内容を読むことがない（PC 側も `os.replace` で半端ファイルを配信しない二重防御）。

**一方向性の担保（§3 の中核不変条件）**:
- DevKit は **GET（受信）のみ**。PC へ **POST/PUT で書き戻さない**（経路を構造的に持たせない）。
- 既読カーソルは DevKit 側ローカルで完結し、PC/組織状態を更新しない（副作用ゼロ, §3.2）。
- コードレビュー観点に「on-device 経路から PC への送信メソッドを呼ばない」を追加する。

**事実 / 要検証 の分離（最重要）**:
- ✓ 事実: ability が**端末ローカルファイルを `open()` で読む**機構は §M3.1（稼働中 repo コード）で確認済。
- ≈ 要検証: **ability プロセスが outbound LAN 通信（HTTP GET）を行えるか**は OpenHome 公式 doc に
  明記がない（docs.openhome.com に Local/Background Ability のネットワーク権限の記載なし。
  Python プロセスとしては技術的に可能だが、appliance の sandbox 制約は未確認）。M3 実機検証で確定する。
- ≈ 要検証: ability とは別の**小さな fetch プロセス**を DevKit 上に常駐させ、取得結果を
  ローカルファイルへ落として ability は従来どおり `open()` する、という分離案が取れるか
  （OpenHome OS 上で ability 以外の常駐プロセスを動かせるか未確認）。

**代替案（HTTP pull が取れない/不適な場合, いずれも 要検証）**:

| 代替 | 仕組み | 長所 | 短所 / 要検証 |
|------|--------|------|----------------|
| **push: scp/rsync** | PC が DevKit へキューファイルを push、ability は従来どおり `open()` | ability に変更不要（読み取り機構そのまま） | DevKit で **sshd 有効化が必要**。OpenHome OS が SSH を開けるか未確認。受信面が増える |
| **共有マウント (SMB/NFS)** | DevKit が PC 共有を mount、ability は mount パスを `open()` | ability に変更不要 | DevKit 側 **mount 設定が必要**（appliance OS で可能か未確認）。Wi-Fi mount の切断耐性に難 |
| **MQTT 等の broker** | PC publish / DevKit subscribe | 疎結合・再送 | broker 追加運用。本件の最小要件に対し過剰 |

> 推奨順位は **HTTP pull > push(scp) > 共有マウント > broker**。
> 決め手は「**ロックダウンされた appliance（DevKit）側に新しい受信サービス/設定を足さない**」こと。
> HTTP pull は DevKit に outbound 通信だけを求め、サーバ責務を完全制御下の PC へ寄せられるため
> 最も単純かつ確実。ただし上記 ≈（ability の egress 可否）が実機検証の最初の確認項目。

### M3.4 デプロイ／エージェント作成（実測）

- ability アップロードは REST: `POST app.openhome.com/api/capabilities/add-capability/`
  （`X-API-KEY`, multipart, **`category=background_daemon`**, `zip_file`）。`npx openhome-cli` でも可。
  > **訂正（M3.1, Refs #11）**: 旧記述は `category=background` だったが、これは**無効値**。
  > 公式 CLI（`openhome-dev/abilities · cli/openhome/abilities.py`）の
  > `VALID_CATEGORIES = ("skill", "brain_skill", "background_daemon", "local")` が一次情報で、
  > 常駐デーモンは **`background_daemon`**。`background` を送ると add-capability は弾く
  > （M3.1 の capability 作成失敗の一因）。multipart フィールドは
  > `name` / `description` / `category` / `trigger_words` / `zip_file`（+任意 `image_file` /
  > `personality_id`=自動 install 先 agent id）。
- **エージェント新規作成の REST エンドポイントは公式 doc に記載なし**（get-all / edit はある）。
  依頼者は専用エージェントの REST 作成を許可していたが、未文書化エンドポイントへライブ
  アカウントに当て推量 POST するのは避けるべきと判断し**専用エージェントは未作成**のまま、
  **Dashboard の Quick Creation** で逐語固定の専用エージェントを作る手順を README に記載した
  （この逸脱は完了報告で窓口に明記する）。

### M3.5 end-to-end の到達点

コード経路（bridge atomic write → 端末 JSON → ability `open()` poll → dedup → 逐語
`render_speech` → `send_interrupt_signal()`→`speak()`）は実 SDK 形で実装・整合済み。
**実機での実音声 1 回**は物理工程（flash/接続/deploy/聴取）が要るため依頼者が手元 DevKit で
実施（README の手順書に成功条件＝「逐語で聞こえる／再転写されない」を明記）。bridge 入力は
M1 同様の**概念 notification dict**（public 衛生のため実 org state スキーマは写さない）。

> 注: 実装済みコード経路は exporter/consumer を同一端末に置いた最小形。**本番の PC→DevKit
> 配送**（exporter は PC 側）は §M3.3.1 を参照。consumer 側の読み取り機構は両者で不変。

### M3.6 DevKit ハード／電源／接続要件（公式 doc 調査, Refs #7）

実機検証（Issue #7）に向け、DevKit の電源・接続要件を公式一次情報で確定した。
**凡例**: ✓ = 公式 doc で確認 / ≈ = 要検証。

#### M3.6.1 DevKit のハード実体

- ✓ OpenHome DevKit は**専用機ではなく Raspberry Pi ベース**。**Raspberry Pi Zero 2 W or higher**
  に OpenHome の DevKit OS イメージを flash して使う
  （[OpenHome blog: AI Raspberry Pi support](https://openhome.com/blog/ai-raspberry-pi-support) /
  [Devkit Setup Guide](https://docs.openhome.com/devkit_setup_guide)）。
- ✓ セットアップガイドの Raspberry Pi Imager 手順では **"Raspberry Pi Zero 2W"** を選択し、
  OpenHome の custom image を書き込む（[Devkit Setup Guide](https://docs.openhome.com/devkit_setup_guide)）。
  ⇒ **文書化された主対象は Zero 2 W**。
- ≈ "or higher"（Pi 4 / Pi 5）でも動くかは blog の表現どまりで、OpenHome イメージの Pi 4/5
  対応可否は公式に未確認（要検証）。電源仕様も後述のとおり Zero 2 W と異なる。

#### M3.6.2 電源仕様（① 電源）

- ✓ OpenHome 公式手順: 「**Connect your Raspberry Pi to a 2Amp minimum charger** and turn it on」
  ＝ **2A 以上のチャージャー**で給電（[Devkit Setup Guide](https://docs.openhome.com/devkit_setup_guide)）。
  あわせて **Raspberry Pi 推奨チャージャー**の使用を案内。
- ✓ Zero 2 W のコネクタは **micro USB**。Raspberry Pi 公式の推奨電源は
  **Raspberry Pi 12.5W Micro USB Power Supply = 出力 +5.1V DC / 2.5A / 12.5W**
  （[RPi 12.5W micro USB PSU product brief](https://datasheets.raspberrypi.com/power-supply/micro-usb-power-supply-product-brief.pdf) /
  [製品ページ](https://www.raspberrypi.com/products/micro-usb-power-supply/) /
  [Pi Zero 2 W 製品ページ](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/)）。
  ⇒ **推奨アダプタ: 5.1V/2.5A micro-USB（公式 12.5W PSU）。最低でも 2A**。
- **PC 給電は不要・非推奨**: DevKit は**自前の AC アダプタ（micro-USB）で独立給電**する。
  PC の USB から給電する前提は公式手順に無く、2A を満たさない PC ポートでは電圧降下の懸念。
  ⇒ **PC からの給電に依存しない**（事実: 公式手順が AC チャージャー前提）。
- ≈ Pi 4 / Pi 5 を使う場合はコネクタが **USB-C** に変わり、公式 PSU も Pi 4=5.1V/3A(15W)・
  Pi 5=5.1V/5A(27W) と異なる。ただし前項のとおり OpenHome イメージの Pi 4/5 対応自体が要検証のため、
  **Zero 2 W（micro-USB / 5.1V・2.5A）を基準**とする。

#### M3.6.3 接続要件（② PC 接続要否・インターネット要否）

- ✓ **インターネット接続は必須**。初回セットアップで DevKit が AP **`Openhome_MACADDRESS`** を
  立て、そこに接続して **Wi-Fi 設定 + OpenHome アカウントでログイン**する
  （[Devkit Setup Guide](https://docs.openhome.com/devkit_setup_guide)）。エージェントの LLM は
  クラウド側のため、運用時もインターネット接続が前提。
- ✓ **PC とのデータ接続は運用には不要**。PC が要るのは **SD カードへの image 書き込み（flash）時のみ**
  （Raspberry Pi Imager。[Devkit Setup Guide](https://docs.openhome.com/devkit_setup_guide) /
  [blog](https://openhome.com/blog/ai-raspberry-pi-support)）。flash 後の設定・操作は
  **iOS アプリ / OpenHome Client と Wi-Fi 経由**で行い、PC への有線/USB データ接続は不要。
- ✓ 音声 I/O: **USB マイク**（default input を `analog-mono`）＋ **Bluetooth スピーカー**（profile `a2dp-sink`）
  （[Devkit Setup Guide](https://docs.openhome.com/devkit_setup_guide)）。
- **本件固有の補足**: OpenHome 単体では PC 不要だが、**我々の連携は組織イベントが PC 上で発生する**ため、
  運用上 PC↔DevKit の**アプリ層の経路**（§M3.3.1 の HTTP pull 等）が別途必要になる。
  これは OpenHome の要件ではなく**本アーキテクチャの要件**である点に注意（混同しない）。

---

## M3.1-sandbox. add-capability sandbox 準拠再設計（Refs #11）

実機検証（Issue #7）の手前で、OpenHome の `add-capability` 静的 sandbox スキャンが
旧 ability バンドルを弾いた。原因は (a) 生 `open()` ベースのファイル transport、
(b) `os` / `import sys` / module 直下の encode import / 低レベル signal 使用、
(c) 無効な `category=background`。これらを一次情報で確定し再設計した。

### M3.1-s.1 禁止集合（一次情報）

| ソース | 禁止 |
|--------|------|
| OpenHome SDK reference "sandbox rules" | 低レベル OS アクセス (`os`)、module 直下の encode import (`json` を top-level に置く)、低レベル signal、生のファイルオープン、`pickle`/`exec`/`eval`、platform internals (`redis`/`user_config`/`connection_manager`) |
| `openhome-dev/abilities · validate_ability.py`（repo PR validator, 逐語確認） | 正規表現で 生のファイルオープン `open(`、`print(`、`assert`、`asyncio.sleep(`/`asyncio.create_task(`、`exec(`/`eval(`、`pickle.`/`dill.`/`shelve.`/`marshal.`、`hashlib.md5(`。import ブロックは `redis`/`user_config` |
| add-capability サーバ応答（実 upload で観測） | **dunder attribute access**。`expr.__name__` 形の属性アクセス（introspection escape）を `Suspicious dunder attribute access` で弾く。`getattr(obj, '__x__')` 系も含む。**dunder メソッド定義（`def __init__` / `def __post_init__`）・`from __future__ import` ・module 直下 `__all__` は attribute access ではないため可**（稼働 ability が依存） |

> **dunder の取りこぼし修正（Refs #11, 2nd round）**: 初回 upload は静的 sandbox lint を
> 通過したが、サーバが `schema.py` の `cls.<dataclass field map>`（dunder 属性アクセス）を
> `Suspicious dunder attribute access` で弾いた。対応: (a) `AnnounceItem.from_dict` を
> 明示的フィールド名タプル `ITEM_FIELDS` 走査に変更し dunder 属性アクセスを除去
> （`tests/test_schema.py` が dataclass フィールドとの同期を担保）、(b) `sandbox_lint.py` に
> **AST ベースの dunder 属性アクセス検出**（`ast.Attribute` の `__x__` + `getattr` 系の dunder
> 文字列）を追加し regression test 化。`@dataclass`/`asdict()` は source に dunder リテラルを
> 持たないので可（静的走査はソーステキスト対象、実行時の dataclass 内部は無関係）。

> 両者の **union** を満たせばどちらのスキャンも通る。SDK reference は低レベル signal を
> 「docstring/コメント内でも」検知すると述べるため、安全側に倒し **バンドル対象ファイル
> （`openhome_ability/*.py` + `approval_voice/*.py`）の docstring/コメントからも禁止
> リテラルを排除**した（詳細根拠は非バンドルの本 doc / DEPLOY.md に集約）。この union を
> `deploy/sandbox_lint.py` が機械化し、`build_zip.py` のビルド時ゲート + 
> `tests/test_sandbox_compliance.py` の単体テストで、ライブアカウントに当てる前に検知する。

### M3.1-s.2 ファイル協調: storage-name ベース API（生 open() 廃止）

ability は file path / 生 `open()` をやめ、`capability_worker` の **storage-name ベース
非同期 API** を使う（稼働中 `community/alarm-timer/background.py` で逐語確認）:

```python
exists = await self.capability_worker.check_if_file_exists("announce_queue.json", False)
raw    = await self.capability_worker.read_file("announce_queue.json", False)
await self.capability_worker.delete_file("announce_seen.json", False)   # delete→write で
await self.capability_worker.write_file("announce_seen.json", text, False)  # 破損回避
```

- いずれも **async（await 必須）**、第 2 引数は temp フラグ（`False`=永続）。
- store 名は固定定数（`announce_queue.json` / `announce_seen.json`）。env 変数は `os` 禁止の
  ため使えず、storage は capability ごとに namespaced なので固定名で正しい。
- 永続化は alarm-timer に倣い **delete→write**（半端 / 追記書き込みを避ける）。

### M3.1-s.3 同梱サブパッケージの解決: 相対 import（sys.path 撤去）

`background.py` / `main.py` は純ロジック `approval_voice` を **相対 import**
（`from .approval_voice.bridge import ...`）で解決し、旧 `sys.path.insert(...)` を撤去した。
ability バンドルは**ラップフォルダごとパッケージとしてロード**されるため、同梱サブ
パッケージは相対 import で解決する。一次根拠は稼働中 `community/dungeon-master-voice/main.py`
の `from .dm_personalities import DM_REGISTRY`（sys.path 操作なし）。

→ 帰結: **zip はラップフォルダ必須**（`build_zip.py --root-folder`, 既定 `approvalvoice`）。
フラット配置だと `background.py` がパッケージメンバにならず相対 import が壊れる。
純ロジックは os/json/生ファイルアクセスを一切持たない **pure logic** に削ぎ、encode/decode と
storage I/O は ability 層へ寄せた（`items_from_raw`/`items_to_payload` /
`seen_from_raw`/`seen_to_payload`）。`json` は **メソッド body 内 import**。

### M3.1-s.4 seed の所在: daemon の self-seed（§M3.1-s.6 で再設計）

旧 `deploy/seed_queue.py`（端末上で file path にサンプルをコピー）は storage-name モデルでは
ability が読めず無効化（削除済み）。当初は **interactive `main.py` をトリガして seed** する形に
したが、後述 §M3.1-s.6 のとおり background_daemon は trigger を持たず `main.py` が voice 起動
されないため、**daemon (`background.py`) 自身が起動時に self-seed** する形へ再設計した。

### M3.1-s.5 本番 transport（§M3.3.1）への含意 — Issue #7 の open item

§M3.3.1 は「PC 側 exporter が JSON を書き、DevKit の ability が file/HTTP で読む」前提だった。
M3.1 で ability の読み取り口が **`capability_worker` storage（ランタイム管理・端末ローカル）**
に変わったため、**PC 側の live org state をどうやって ability の storage へ届けるか**は
別問題として残る（ability は自分の storage しか読めない）。本タスク（M3.1）のスコープは
sandbox 準拠 + daemon の self-seed（§M3.1-s.6）による on-device スモークまでで、live org state →
端末 storage の本番配送は **Issue #7（on-device end-to-end）の確定事項**とする。候補は (a) ability 自身が poll
ごとに LAN の exporter へ outbound GET し取得結果を `write_file` で自 storage に落とす
（ability egress 可否は §M3.3.1 の ≈要検証）、(b) interactive trigger 駆動で都度 seed、など。

### M3.1-s.6 daemon ライフサイクルと self-seed（Refs #11, 3rd round）

初回 upload は sandbox を通過し capability 登録に成功した（capability_id=6363）が、Dashboard が
**"No triggers for this ability"** を表示し、seed が走らない構造疑義が出た。一次情報で確定:

| 論点 | 一次情報（公式 doc / 稼働 ability） | 結論 |
|------|-----------------------------------|------|
| daemon の起動 | background-abilities doc: *"Starts automatically on session"* / *"No hotword trigger needed"* | **session 開始時に自動起動・trigger 不要** |
| trigger words | 同 doc: background-only は trigger を持たない | **"No triggers" は background_daemon の正常表示**（バグではない） |
| interactive + daemon 同居 | 同 doc "Interactive + Daemon" / 稼働 `community/alarm-timer`（main.py が `oh_alarms.json` を write、background.py が poll、両者は storage 経由でのみ協調） | **1 capability に同居可**。canonical パターン |
| 初期 seed | 同 doc: *"The JSON file may not exist yet if main.py hasn't been triggered. Always check_if_file_exists() first."* | daemon は「未 seed」を許容すべき。だが **main.py が trigger されないと queue は空のまま** |

→ **問題の核**: `category=background_daemon` では `main.py` が voice 起動されないため、
seed を main.py に置くと daemon が空 queue を poll し続け、何も読み上げられない。

→ **採用設計（self-seed）**: daemon は自動起動するので、**`background.py` が起動時に
`SMOKE_AUTOSEED` が真なら 4 ゲートサンプルを自 storage へ write + 既読カーソル reset** し、
その後 poll ループで逐語読み上げする。trigger も SSH も interactive も不要。session 再起動の
たびにフレッシュに 4 件読み上げる（再テスト = session 再起動）。seed データ／フラグは
`approval_voice/sample.py`（`SAMPLE_NOTIFICATIONS` / `SMOKE_AUTOSEED`）に隔離し、
**Issue #7 は `SMOKE_AUTOSEED = False`** にするだけで daemon は real exporter データのみ読む。

- `main.py` は canonical な "Interactive + Daemon" の interactive 側として残置（必須ファイル +
  trigger が露出する構成なら導通確認に使える）が、**スモークは main.py に依存しない**。
- 却下案: (a) interactive trigger を効かせて main.py で seed → background_daemon では trigger 非露出で
  不確実。(b) zip を 2 つに分割 → 過剰、storage 協調の canonical パターンに反する。
- 検証: 実 daemon ロジックを fake `capability_worker` で駆動し、self-seed→割り込み 1 回→4 件読み上げ
  →2 周目 dedup を確認（`build_zip` verify はパッケージ import + データ経路、本シミュレーションは
  ループ挙動）。

### M3.1-s.7 サーバ側 禁止 module 一覧（実測ベース・Refs #11, 4th round）

add-capability は禁止モジュールの **import** を `{"detail":"Forbidden module import: <name>"}`
で弾く。これは static sandbox lint（os/json/open/signal/dunder）とは**別レイヤ**で、
ローカル lint が clean でもここで落ちる。診断用に入れた `import traceback` が
**`Forbidden module import: trace`** で弾かれて判明（method-local import でも検出される）。

| 観測 / 分類 | module |
|------|--------|
| **実測で弾かれた** | `traceback`（エラー文言は `trace`。prefix/独立かは未確定だが両方を denylist） |
| debug / introspection / profiling（同類として denylist） | `trace` `traceback` `pdb` `bdb` `cProfile` `profile` `pstats` `dis` `inspect` `faulthandler` `ctypes` |
| unsafe serialization | `pickle` `dill` `shelve` `marshal` |
| platform internal | `redis` `user_config` `connection_manager` |

- 公式 doc に完全な禁止 module 一覧は**非公開**。本表は実測 + 明らかな同類から構成した
  **denylist**で、`deploy/sandbox_lint.py` の `_FORBIDDEN_IMPORT_MODULES` に集約。
  **新たな "Forbidden module import" 観測があればこの集合に 1 行追加するだけ**で、build_zip の
  ビルドゲートと test が以後それを捕捉する（単一の保守点）。
- 例外詳細のログは `traceback.format_exc()` をやめ **`repr(e)`**（= `"ExceptionType('msg')"`、
  type 名 + message）に変更。`type(e).__name__` は **dunder 属性アクセス（§M3.1-s.1）で別途禁止**
  なので使わない。`repr(e)` は forbidden module も dunder も踏まない。
- **未知の禁止パターンが残存する可能性**: サーバ sandbox 仕様が非公開のため、別レイヤ（禁止
  module / dunder / 静的パターン）でさらに発見されうる。発見ごとに対応レイヤへ denylist 追加する。

---

## 付録: 参照

- 社内既存リサーチ「OpenHome × 組織システム × renga 連携検討資料」(2026-05-31)
  — OpenHome の Agent/Ability モデル、CapabilityWorker SDK、Background Ability、
  接続点（WebSocket / REST / Local Connect / Local Abilities）の一次情報調査。
  本件はその **A-5（承認ゲート音声化）の一方向サブセット**、
  姉妹 ambient-announcer は **A-4（常駐アナウンサー）**に対応する。
- OpenHome 公式 doc: SDK Reference / WebSocket / Building Abilities / Background Abilities
  （上記リサーチの出典欄を参照）。
