# approval-voice デプロイキット（DevKit 実機スモーク）

OpenHome DevKit 上で approval-voice ability を動かし、サンプルキューを投入して
**逐語で喋るのを聴いて確認**するための turnkey 手順。コピペで進められるよう構成。

前提（依頼者側で完了済み）: DevKit を起動・ネット接続・専用エージェント作成済み。

> 🔑 **鍵の扱い**: 本書のコマンドはすべて `OPENHOME_API_KEY` / `<AGENT_ID>` を
> **プレースホルダ**にしている。実鍵・実 agent id をリポジトリに残さないこと。
> 鍵はシェルの環境変数で渡す（`npx openhome-cli` は鍵をローカル disk に永続化する
> ため、依頼者の手元マシンでのみ使用しリポには持ち込まない）。

---

## 0. 最短手順（番号付きサマリ）

1. **zip を作る**（開発 PC）: `py -3 deploy/build_zip.py` → `dist/approval-voice-ability.zip`
   （既定でラップフォルダ `approvalvoice/` 付き。生成時に **sandbox lint** + 展開→パッケージ
   import の自動検証）
2. **アップロード**: `npx openhome-cli` か REST `add-capability`（§2。**`category=background_daemon`**）
3. **対象エージェントへ install/enable**（§3。agent id は §3.1 で調べる）
4. **enable → セッション開始**（§4）: daemon が自動起動し、self-seed（`SMOKE_AUTOSEED=True`）で
   サンプル 4 ゲートを storage に書き込む。trigger も seed_queue も SSH も不要
5. **読み上げ確認**（§5）
6. **成功条件を確認**: 逐語で聞こえる／自発話が再転写されない（§5.3）

---

## 1. デプロイ zip をビルドする（開発 PC）

```bash
# 既定: dist/approval-voice-ability.zip を生成し、展開→import を自動検証
py -3 deploy/build_zip.py
```

ビルドは **単一の真実源** を守る: 実機 ability `openhome_ability/`（main.py /
background.py / __init__.py / requirements.txt）をラップフォルダ `approvalvoice/`
直下へ、純ロジック `approval_voice/` をそのさらに下へ**そのまま同梱**するだけで、
重複実装は作らない。

### zip レイアウト（既定 = ラップフォルダ `approvalvoice/`、相対 import に必須）

```
approval-voice-ability.zip
└── approvalvoice/         ← ラップフォルダ（= パッケージ）
    ├── main.py            ← interactive entry（status 読み上げのみ・必須ファイル）
    ├── background.py      ← background daemon（storage polling→逐語 speak）
    ├── __init__.py        ← パッケージマーカ（相対 import に必須）
    ├── requirements.txt   ← stdlib only（追加依存なし）
    └── approval_voice/    ← background.py が相対 import する純ロジック（同梱必須）
        ├── __init__.py
        ├── schema.py  renderer.py  poller.py  bridge.py  storage.py
        ├── ability.py  speak.py
```

> ⚠️ **レイアウトの肝（M3.1 で変更）**: `background.py` / `main.py` は同梱した
> `approval_voice` を **相対 import**（`from .approval_voice...`）で解決する（旧
> `sys.path.insert(...)` は add-capability sandbox で禁止のため撤去）。相対 import は
> ability バンドルが**パッケージとしてロード**される前提なので、**ラップフォルダが必須**
> （フラット配置だと相対 import が壊れる）。一次根拠は稼働中 `dungeon-master-voice/main.py`
> の `from .dm_personalities`。

### sandbox 準拠 + import 健全性の事前検証（自動）

`build_zip.py` は次を自動で行う:

1. **sandbox lint** (`deploy/sandbox_lint.py`): 同梱する全 .py を OpenHome add-capability の
   禁止パターン（低レベル OS アクセス / module 直下の encode import / 生のファイルオープン /
   低レベル signal / pickle/exec/eval/print/assert 等）で走査。違反があれば**ビルドを止める**。
2. **パッケージ import 検証**: zip を temp へ展開し、**別プロセス**で
   `import <wrap>.background` / `import <wrap>.main`（= 実ランタイムと同じパッケージ形）を実行。
   `src.*` は最小スタブ、`approval_voice` は同梱の本物を相対解決させる。
   → 「ラップフォルダ配下で `from .approval_voice...` が解決する」＝相対 import/パス不一致が
   無いことを実証。
3. 続けて `examples/announce_queue.json`(4 ゲート) を
   `items_from_raw → ReadCursor → render_speech` に流し 4 件、daemon の self-seed サンプル
   （`approval_voice/sample.py`）も bridge を通って 4 件になることを確認。

成功すると `[sandbox] ... scan OK` と `[verify] ability import + data path OK (VERIFY_OK 4)` が出る。

> ℹ️ ラップフォルダ名を変えたい場合は `--root-folder <name>`（相対 import は名前非依存）。
> 空文字（フラット）は相対 import が壊れるため verify が失敗する。

---

## 2. アップロード

### 2.1 npx openhome-cli（CLI）

```bash
export OPENHOME_API_KEY="<伏字: あなたのAPIキー>"

# サブコマンド/フラグ名は環境で異なりうるので、まず help で確認すること
npx openhome-cli --help
```

> ℹ️ `openhome-cli` の正確なサブコマンド名は公開ドキュメントで確定できなかった。
> help で capability 追加コマンドを確認できない場合は §2.2 の REST を使えば確実。

### 2.2 REST（add-capability）— 確実な経路

`docs/design.md` §M3.4 で確定した契約。`X-API-KEY` ヘッダ + multipart、
**`category=background_daemon`**、`zip_file` にビルド済み zip を載せる。
`name` / `category` / `description` / `trigger_words` / `zip_file` が必須。

> ⚠️ **`category=background_daemon`**（常駐デーモン）。公式 CLI の
> `VALID_CATEGORIES = ("skill", "brain_skill", "background_daemon", "local")` が一次情報で、
> 旧 `category=background` は**無効値**（add-capability に弾かれる、M3.1 失敗の一因）。
> `name` は**英数字のみ**（`approvalvoice`）。任意で `image_file`、`personality_id`
> （自動 install 先 agent id）も渡せる。

```bash
export OPENHOME_API_KEY="<伏字: あなたのAPIキー>"

curl -sS -X POST \
  "https://app.openhome.com/api/capabilities/add-capability/" \
  -H "X-API-KEY: $OPENHOME_API_KEY" \
  -F "name=approvalvoice" \
  -F "category=background_daemon" \
  -F "description=Secretary の承認待ちを逐語で読み上げる一方向 ability" \
  -F "trigger_words=承認読み上げ, approval voice" \
  -F "zip_file=@dist/approval-voice-ability.zip"
```

レスポンスに作成された capability の id が返る想定（install に使う場合がある）。

---

## 3. 対象エージェントへ install / enable

### 3.1 agent id の調べ方（get-all agents）

```bash
# get-all は documented（design.md §M3.4）。正確なパスはダッシュボードを開いた状態で
# DevTools の Network タブを見て、agent 一覧を返すリクエストで裏取りするのが最も確実。
curl -sS -X GET \
  "https://app.openhome.com/api/agents/get-all-agents/" \
  -H "X-API-KEY: $OPENHOME_API_KEY"
```

返ってきた一覧から、専用エージェントの `id` を控える（以後 `<AGENT_ID>`）。

### 3.2 install / enable

```bash
export AGENT_ID="<伏字: 対象エージェントの id>"
```

> ℹ️ install/enable の正確なエンドポイントは公開ドキュメントで確定できなかった。
> ダッシュボードで「capability を agent に追加→有効化」する操作を Network タブで観察し、
> 実際のメソッド/パス/ボディに合わせること。`category=background_daemon` の capability は
> 追加後に enable（有効化）が必要な場合がある。`npx openhome-cli --help` の
> install 系サブコマンドでも可。

---

## 4. サンプルキューを seed する（SSH 不要・storage-name モデル）

M3.1 で ability は **file path / 生 open() をやめ**、`capability_worker` の
storage-name ベース API（`read_file`/`write_file`/`check_if_file_exists`/`delete_file`,
第2引数 False=永続）でファイル協調する。よって**外から file path にコピーする旧
`seed_queue.py` は無効**（ability はその file path を読まない）になり、削除した。

### 4.1 storage 名（固定）

| 用途 | storage 名 | 備考 |
|------|-----------|------|
| 読み上げキュー | `announce_queue.json` | §1.3 アイテムの配列。ability が**読み取り専用**で扱う |
| 既読カーソル | `announce_seen.json` | spoken id の配列。ability 側ローカルで永続（副作用ゼロ） |

poll 間隔は `approval_voice/storage.py` の `POLL_SECONDS`（既定 15 秒）。env 変数は
`os` 禁止のため使わない。storage は capability ごとに namespaced なので固定名で正しい。

### 4.2 seed の流し方（daemon の self-seed・トリガ不要）

**background_daemon は session 開始時に自動起動し trigger を持たない**（公式 doc:
"Starts automatically on session" / "No hotword trigger needed"）。よって Dashboard の
**"No triggers for this ability" は正常表示**で、`main.py` は voice 起動されない。

そのため seed は **daemon (`background.py`) 自身が起動時に行う**: `SMOKE_AUTOSEED=True`
（`approval_voice/sample.py`）のとき、

1. 既読カーソル `announce_seen.json` を delete（フレッシュ読み上げのため reset）、
2. サンプル 4 ゲート（worker_complete / ci_merge / escalation / reply_relay を 1 件ずつ）を
   `write_file` で `announce_queue.json` に self-seed、
3. その後 poll ループで 4 ゲートを逐語読み上げ。

**再試行はセッション再起動だけ**（daemon が再起動のたびに reset + 再 seed）。SSH も trigger も
seed_queue も不要。

> ℹ️ **既定は本番モード（`SMOKE_AUTOSEED=False`）**: `approval_voice/sample.py` の
> 既定値は **False**（self-seed しない＝ real exporter データのみ読む）。トリガ不要の
> 端末内完結スモークを回したいときだけ **True に戻す**（起動時に 4 ゲートを再 seed +
> 既読カーソル reset）。本番の live データ供給は §4.3 を使う。

### 4.3 本番の live データ供給（PC->DevKit, §M3.3.1）

self-seed を止めた本番では、PC 側 exporter が組織の `awaiting_user` 状態を read-only で読み、
§1.3 キュー JSON を生成して DevKit の daemon の storage（`announce_queue.json`）へ届ける。
daemon 側は **自 storage を読むだけ**（dedup/render/speak は不変）。届け方は 2 経路:

#### (A) primary: requests HTTP pull（別トラックで実装）

> **経験則で確定（add-capability 実測）**: ability バンドルに `import requests` を入れた
> capability は **受理された（HTTP 201）**。公式 doc / 30+ の shipped ability も `requests` を
> sanctioned outbound として使用する。一方 `urllib` / `http.client` / `socket` は
> **forbidden import で reject**（urllib は HTTP 400）。よって本番 primary は
> **`requests` ベースの HTTP GET pull**（PC 側 exporter が `py -3 -m pc_exporter serve` で
> §1.3 を LAN 配信、DevKit が GET）で、これは**別トラック / 別 PR**で実装する。
>
> ⚠️ ただし **socket 層は共有**のため、`requests` が受理されても **device 上で実際に egress
> できるか**は別問題で、**実機 GET で初めて確定**する（≈要検証）。これが実機検証の第 1 項目。

#### (B) fallback: PC-side push（scp/sftp, 本 PR）

primary の egress が device で**失敗した場合の正式 fallback**（design.md §M3.3.1 の推奨順位
「HTTP pull > **push(scp)** > 共有マウント > broker」どおり）。**ability 側は一切ネットワーク
しない**（urllib を撤去済み・bundle に network import ゼロ）。代わりに **PC が DevKit へキュー
ファイルを push** し、daemon は従来どおり自 storage を読む。

```bash
# PC 側で常駐: state.db を read-only で読み → §1.3 を atomic 書込 → DevKit へ scp/sftp
py -3 -m pc_exporter push \
    --db-path <claude-org>/.state/state.db \
    --target user@devkit:/<ability-storage-path>/announce_queue.json \
    --interval 2
# 単発配送: 末尾に --once。鍵指定: --identity <key>。SSH ポート: --port <n>。
```

- **paramiko ベース**（`pc_exporter/push.py`）。stdlib だけだと `scp.exe` 呼び出しになり Windows
  で不安定なため。`pip install -r pc_exporter/requirements.txt`（PC 側のみ・bundle 非同梱）。
- **冪等**: `core.export_queue` は毎ループ atomic 書込で **mtime が必ず変わる**ため、素朴な mtime
  比較では skip できない。よって **内容の SHA-256 ダイジェスト**を冪等キーにし、**前回配送と
  同一バイト列なら再 push しない**。ただし**リモートのファイルが消えた / サイズ不一致**（DevKit
  再起動でストレージが飛んだ等）の場合は、内容が同じでも**再 push** する（device を空のまま
  取り残さない）。
- **再送 / backoff**: 転送失敗時は指数バックオフ（1s,2s,4s… 上限 30s）で `--attempts`（既定 5）
  回まで再試行。ループ mode では失敗した round は次 interval でリトライ（クラッシュしない）。
- **リモート atomic**: `<path>.tmp` へ put → `posix_rename` で原子的に swap（daemon が半端な
  ファイルを読まない）。ローカル target（共有マウント/テスト）は temp+`os.replace`。
- **一方向厳守**: push は **PC -> DevKit のみ**。device から org 状態を読み戻す経路は無い。
  ability 側は send 経路ゼロ（outbound GET も撤去済み・`tests/test_outbound_one_way.py` が AST 担保）。
  push が device 状態を読むのは再 push 判定の `stat()` のみ。
- **`--target` がローカルパス**（`host:` 接頭辞なし）なら **local copy transport** で配送。PC に
  mount した DevKit 共有（§M3.3.1「共有マウント」）への drop や、loopback テストに使う。

> 🔴 **未解決の open question（実機調査必要）**: push の `--target` パスが **ability の
> `capability_worker` storage 実体に一致するか**は **未確認**。OpenHome SDK Reference は storage を
> **役割**でしか規定せず（`in_ability_directory=False` = "user data storage, shared across that
> user's abilities"）、**具体的な on-disk パスを公開していない**。かつ ability は任意パスを
> 低レベル `open()` で読めない（sandbox 禁止）ため、別の場所に push して ability が読む、という
> 迂回もできない。**実機で `capability_worker.write_file(name, ..., False)` の実 storage パスを
> 特定し、その場所を `--target` に指定する**のが本 fallback の最初の実機ステップ。特定不能なら
> 共有マウント（DevKit 側で PC 共有を mount し、storage 実体をそのマウント配下に置けるか）や
> requests pull（primary）へ寄せる。本 PR の push.py は **transport のみ**を提供し、`--target` が
> 指す場所へファイルを確実に届けるところまでを保証する。

---

## 5. DevKit 実機チェックリスト

### 5.1 起動（自動）

§3 で enable した capability の **background daemon (`background.py` の
`ApprovalVoiceWatcher`)** は、**エージェントとのセッションが始まると自動起動**する
（trigger 不要）。起動時に self-seed（§4.2）し、storage の `announce_queue.json` を
`POLL_SECONDS`（既定 15）秒ごとに polling する。storage 名・poll 間隔は固定
（`approval_voice/storage.py`）で env 設定は不要（`os` 禁止のため）。

### 5.2 読み上げ

- daemon は起動 → self-seed → 次の poll（最大 `POLL_SECONDS` 秒）で未読 4 ゲートを検知し、
  `send_interrupt_signal()`（1 回）→ 4 ゲートを順に逐語読み上げ（手動トリガ不要）。
- interactive 側（`main.py`）は status 読み上げのみの導通確認で、trigger が露出する構成なら
  voice 起動できるが、**スモークは main.py に依存しない**。

### 5.3 成功条件

- [ ] **逐語で聞こえる**: 「ワーカー完了の承認待ちです。…選択肢は、1 承認、2 差し戻し。
      返事は端末でお願いします。」のように、`render_speech` の文面が**そのまま**
      （言い換えなし）読み上げられる。
- [ ] **4 ゲートとも読み上げられる**（worker_complete / ci_merge / escalation / reply_relay）。
- [ ] **自発話が再転写されない**: ability 自身の発話を OpenHome が再度ユーザ入力として
      拾わない（M1 最重要要件＝一方向）。読み上げ後にループ・自走しないことを確認。
      （`send_interrupt_signal()` を speak 前に 1 回呼ぶことで構造的に担保済み）
- [ ] **二重読み上げしない**: 同じ poll を跨いで再読しない（seen カーソルで dedup）。

### 5.4 トラブルシュート

- "No triggers for this ability" 表示: **正常**（background_daemon は trigger を持たない）。
- 何も聞こえない: capability が agent に install/enable されているか、**セッションを開始したか**
  （daemon は session 開始で自動起動）。起動後 self-seed → 次の poll（最大 `POLL_SECONDS` 秒）まで待つ。
- import エラー（add-capability で弾かれる / 起動しない）: zip が**ラップフォルダ付き**か
  （既定の `py -3 deploy/build_zip.py` で付く）、`category=background_daemon` か（`background`
  は無効値）を確認。`build_zip` の sandbox lint + verify がローカルで通っていれば構造は健全。
- 1 回読んだ後 2 回目が鳴らない: 正常（seen で dedup 済み）。再試行は**セッション再起動**だけ
  （daemon が再起動時に seen を reset して同じ 4 件を再 seed する）。

### 5.5 ログ取得と原因切り分け（実機診断・Refs #11）

daemon は各ステップを `editor_logging_handler.info` で出力する（`[ApprovalVoice] ...`）。
**第一情報源は Dashboard の "Open In Editor" → log タブ**（このハンドラの出力先）。

**期待されるログ列**（正常時、上から順に出る）:

```
[ApprovalVoice] call() entered (background_daemon_mode=..., SMOKE_AUTOSEED=True) — creating watch_queue task
[ApprovalVoice] watch_queue: task started (SMOKE_AUTOSEED=True, background_daemon_mode=...)
[ApprovalVoice] smoke_seed: start
[ApprovalVoice] smoke_seed: built payload (4 gate(s), 972 chars)
[ApprovalVoice] smoke_seed: seen exists=False
[ApprovalVoice] smoke_seed: queue exists=False
[ApprovalVoice] smoke_seed: wrote 4 gate(s) to announce_queue.json -> end
[ApprovalVoice] background.py ACTIVE — polling storage announce_queue.json every 15.0s
[ApprovalVoice] poll tick=1: queue_exists=True raw=972chars items=4 seen=0 fresh=4
[ApprovalVoice] read_aloud: start (4 gate(s)); sending interrupt
[ApprovalVoice] read_aloud: interrupt sent
[ApprovalVoice] read_aloud: speak 1/4 (gate=worker_complete, 88 chars)
[ApprovalVoice] read_aloud: speak 1/4 done
... (2/4, 3/4, 4/4) ...
[ApprovalVoice] read_aloud: end
[ApprovalVoice] saved read-cursor (4 id(s))
```

**どこで止まったか → 原因の早見表**:

| 最後に出たログ | 推定原因 / 次の手 |
|---|---|
| （何も出ない） | daemon が起動していない。capability が agent に **install + enable** 済みか、background_daemon として登録されているか、セッションを開始したかを確認 |
| `call() entered` まで | `watch_queue` タスクが回っていない（runtime の task scheduling）。`background_daemon_mode` の値をログで確認 |
| `smoke_seed: start` 付近で `smoke autoseed error: ... <traceback>` | storage API（`write_file`/`check_if_file_exists` 等）のシグネチャ/挙動差。traceback を窓口へ |
| `smoke_seed: ... -> end` は出るが `poll tick=1: queue_exists=False` | `write_file` が永続していない（temp フラグ/storage 名）。traceback と合わせ確認 |
| `poll tick=1: ... fresh=0`（items=0 や seen=4） | seed と読み取りの storage 名ズレ、または既読カーソルが残存。`SMOKE_AUTOSEED` 値も確認 |
| `poll error (tick=...): ... <traceback>` | read/parse/読み上げ中の例外。traceback を窓口へ |
| `read_aloud: speak 4/4 done` まで**出る**のに**無音** | コード経路は健全 = **audio device 問題**。下記 §5.5.1 |

`SMOKE_AUTOSEED` が False と出ていたら `approval_voice/sample.py` を True に戻す。

#### 5.5.1 speak は走るのに無音（audio device）

`read_aloud: speak ... done` が出ているのにスピーカーから聞こえない場合、ability の発話経路は
正常で **DevKit の音声出力**側の問題:
- **Bluetooth スピーカーが接続・選択されているか**（profile `a2dp-sink`、design.md §M3.6.3）。
- USB マイク/スピーカーの default device 設定。
- OpenHome の他 ability（trigger 起動の通常会話）で音声が出るか試し、audio 自体の導通を切り分ける。
- storage 書込みは `smoke_seed: wrote ...` ログで確認できる（speak とは独立に storage 健全性を確認可能）。

#### 5.5.2 Pi 側 runtime ログ（補助）

editor log タブが取れれば通常は十分。さらに Pi 上の runtime ログを見たい場合は SSH して
OpenHome runtime のサービスログ（`journalctl` 等）を参照する（**サービス名/ログパスは
OpenHome DevKit OS 依存のため実機で確認**。公式 doc に明記なし）。

> 注: 上記の厚いログは M3.1 実機 bring-up 用の診断 instrumentation。読み上げ確認後は
> `background.py` の `_log` 呼び出しを間引いてよい（`[ApprovalVoice]` prefix で grep 可能）。

---

## 6. 既知の「要検証」（スモークの障害にしない）

- **本番（live org state → 端末 storage）の配送**: storage-name モデルでは ability は
  自分の `capability_worker` storage しか読めない。PC 上で発生する live `awaiting_user`
  state をその storage へどう届けるかは 2 経路（§4.3）: **(A) primary = requests HTTP pull**
  （ability が `requests` で GET → `write_file`。`requests` は add-capability で受理＝実測 201）、
  **(B) fallback = PC-side push**（PC が scp/sftp で storage へ配送、ability は読むだけ＝本 PR）。
  残る ≈要検証は順に: ① **device 上で実際に egress できるか**（socket 層共有のため実機 GET で確定。
  失敗なら (B) push へ）、② **push の `--target` が ability storage 実体に一致するか**（SDK は
  storage を役割でしか規定せず on-disk パス非公開＝**実機調査必要**、§4.3 (B) の open question）。
  self-seed スモーク（`SMOKE_AUTOSEED=True`）は egress / 配送非依存で端末内完結するため、配送検証前
  でも読み上げ経路の導通は確認できる。

---

## 付録: ファイル一覧

| パス | 役割 |
|---|---|
| `deploy/build_zip.py` | デプロイ zip 生成（ラップフォルダ既定）+ sandbox lint + 展開→パッケージ import 自動検証 |
| `deploy/sandbox_lint.py` | add-capability 禁止パターンの静的スキャナ（build_zip + test が共用） |
| `examples/announce_queue.json` | canonical 4 ゲートサンプル（§1.3 準拠・test 済） |
| `openhome_ability/background.py` | on-device 常駐 daemon（自動起動・self-seed・storage API で polling・逐語 speak） |
| `openhome_ability/main.py` | interactive 導通確認（status 読み上げのみ・必須ファイル） |
| `approval_voice/sample.py` | スモーク seed データ + `SMOKE_AUTOSEED` フラグ（既定 False=本番） |
| `approval_voice/storage.py` | storage 名 + `POLL_SECONDS`（ネットワーク設定なし＝storage-only reader） |
| `approval_voice/` | 純ロジック（schema/renderer/poller/bridge/storage/sample/...）= 単一の真実源 |
| `pc_exporter/push.py` | **PC-side push fallback**（paramiko scp/sftp・content-hash 冪等・backoff・atomic）= 本 PR |
| `pc_exporter/__main__.py` | exporter CLI（`export` / `serve` / **`push`**） |
| `pc_exporter/requirements.txt` | PC 側 push の依存（`paramiko`・bundle 非同梱） |
