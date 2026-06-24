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
4. **スモーク seed（SSH 不要）**: interactive ability をトリガ → `main.py` が `write_file` で
   サンプル 4 ゲートを storage に seed + 既読カーソル reset（§4）
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
    ├── main.py            ← interactive entry（seed + status 読み上げ）
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
   `items_from_raw → ReadCursor → render_speech` に流し 4 件、`main.py` の seed payload も
   bridge を通って 4 件になることを確認。

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

### 4.2 seed の流し方（interactive ability をトリガ）

DevKit 上で **interactive ability（trigger words = 例「承認読み上げ」/「approval voice」）を
トリガ**すると、`main.py`（`ApprovalVoiceStatus`）が:

1. 既読カーソル `announce_seen.json` を delete（フレッシュ読み上げのため reset）、
2. サンプル 4 ゲート（worker_complete / ci_merge / escalation / reply_relay を 1 件ずつ、
   `examples/announce_queue.json` をミラー）を `write_file` で `announce_queue.json` に seed、
3. status を 1 行読み上げ（導通確認）。

その後 background daemon が次の poll（最大 `POLL_SECONDS` 秒）でキューを検知し 4 ゲートを
逐語読み上げする。**再試行は interactive を再トリガするだけ**（seen が毎回 reset されるので
同じ 4 件がまた読み上げられる）。SSH も seed_queue も不要。

---

## 5. DevKit 実機チェックリスト

### 5.1 起動

OpenHome ランタイム経由で、§3 で enable した capability の **background daemon
(`background.py` の `ApprovalVoiceWatcher`)** が常駐起動し、storage の
`announce_queue.json` を `POLL_SECONDS`（既定 15）秒ごとに polling する。storage 名・
poll 間隔は固定（`approval_voice/storage.py`）で、env 設定は不要（`os` 禁止のため）。

### 5.2 トリガ

- interactive 側（`main.py` の `ApprovalVoiceStatus`）を trigger words で起動すると、
  サンプル 4 ゲートを storage に **seed + 既読カーソル reset** し、status を 1 行喋る
  ＝**導通確認 + seed**（§4.2）。
- その後 background daemon が次の poll（最大 `POLL_SECONDS` 秒）で未読を検知し、
  4 ゲートを順に逐語読み上げ（手動トリガ不要。これが background_daemon category の役割）。

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

- 何も聞こえない: capability が enable されているか / interactive を実際にトリガしたか
  （seed は interactive トリガ起点）。次の poll（最大 `POLL_SECONDS` 秒）まで待つ。
- import エラー（add-capability で弾かれる / 起動しない）: zip が**ラップフォルダ付き**か
  （既定の `py -3 deploy/build_zip.py` で付く）、`category=background_daemon` か（`background`
  は無効値）を確認。`build_zip` の sandbox lint + verify がローカルで通っていれば構造は健全。
- 1 回読んだ後 2 回目が鳴らない: 正常（seen で dedup 済み）。再試行は interactive を
  **再トリガ**するだけ（main.py が seen を reset して同じ 4 件を再 seed する）。

---

## 6. 既知の「要検証」（スモークの障害にしない）

- **本番（live org state → 端末 storage）の配送**: storage-name モデルでは ability は
  自分の `capability_worker` storage しか読めない。PC 上で発生する live `awaiting_user`
  state をその storage へどう届けるか（ability が outbound GET → `write_file` で自 storage へ
  落とす案など）は **Issue #7（on-device end-to-end）の確定事項**。ability の outbound 通信
  可否は §M3.3.1 の ≈要検証。本スモークは `main.py` seed で端末内完結するためブロッカーに
  しない。

---

## 付録: ファイル一覧

| パス | 役割 |
|---|---|
| `deploy/build_zip.py` | デプロイ zip 生成（ラップフォルダ既定）+ sandbox lint + 展開→パッケージ import 自動検証 |
| `deploy/sandbox_lint.py` | add-capability 禁止パターンの静的スキャナ（build_zip + test が共用） |
| `examples/announce_queue.json` | canonical 4 ゲートサンプル（§1.3 準拠・test 済） |
| `openhome_ability/background.py` | on-device 常駐 daemon（`approval_voice` を相対 import、storage API で polling） |
| `openhome_ability/main.py` | interactive 導通確認 + サンプル seed（`write_file`） |
| `approval_voice/` | 純ロジック（schema/renderer/poller/bridge/storage/...）= 単一の真実源 |
