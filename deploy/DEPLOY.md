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

#### (A) primary: requests HTTP pull（**実装済・2026-06-26 live 統合で確定**）

> **経験則で確定（add-capability 実測）**: ability バンドルに `import requests` を入れた
> capability は **受理された（HTTP 201）**。公式 doc / 30+ の shipped ability も `requests` を
> sanctioned outbound として使用する。一方 `urllib` / `http.client` / `socket` は
> **forbidden import で reject**（urllib は HTTP 400）。よって本番 primary は
> **`requests` ベースの HTTP GET pull**（PC 側 exporter が `py -3 -m pc_exporter serve` で
> §1.3 を配信、**cloud 上の ability** が GET）。
>
> 🔴 **実行場所の確定（最重要・2026-06-26）**: ability コードは **DevKit ではなく OpenHome の
> cloud（Ubuntu サーバ）上で実行**される（`docs/design.md` §M3.0。根拠は cloud error log の path
> `/home/ubuntu/.../user_capabilities/<user_id>/...`）。DevKit は **audio I/O 端末**にすぎない。
> よって **pull の `requests.get` は cloud から発する** → **private LAN IP（192.168.x.x）は
> 原理的に不可達**。PULL_URL は **public HTTPS が必須**（採用した経路は §6 の cloudflared
> quick tunnel）。
>
> **egress は確定（≈要検証 → 解消）**: 2026-06-26 の live 統合で **cloud → public HTTPS の
> GET が成功し real gate を逐語読み上げ**できた（`[ApprovalVoice] pull tick=N: GET 200 ...
> -> wrote QUEUE_STORE` を確認）。`requests` 受理だけでなく **実 egress も成立**。
>
> **実装**: `openhome_ability/background.py` の `_pull_into_storage()` が毎 poll tick で
> `requests.get(PULL_URL)` し、200 + §1.3 parse 成功時に body を `QUEUE_STORE` へ
> delete-then-write、その後は不変の read/dedup/render/speak。失敗（endpoint down /
> 非200 / timeout / bad body）時は storage を触らず既存内容を読む。設定は
> `approval_voice/storage.py` の定数 `PULL_ENABLED`（既定 True）/ `PULL_URL`（public HTTPS の
> tunnel URL を埋め込む。例 `https://<random>.trycloudflare.com/announce_queue.json`）/
> `PULL_TIMEOUT`。**env でなく定数**なのは sandbox が `os` を禁止し ability 側が環境変数を
> 読めないため（PC 側 exporter は env 可）。GET のみ＝一方向不変（`test_outbound_one_way.py`）。
>
> 📎 **historical note（不採用の旧前提）**: 当初は「PC=有線 LAN / DevKit=Wi-Fi が同一 LAN」で
> **`PULL_URL=http://192.168.2.103:8731/announce_queue.json` の LAN 直配信**を想定していた。
> これは「ability が DevKit 上で実行される」という誤った前提に基づくもので、**cloud 実行の
> 判明により無効**（cloud から RFC1918 private IP は不可達）。LAN URL 例は参考記録としてのみ残す。

#### (B) fallback: PC-side push（scp/sftp）

> 🔴 **前提が崩れた（2026-06-26 cloud 実行の判明）**: この push fallback は「ability storage が
> **DevKit 上**にあり、PC が scp/sftp で DevKit へ届ける」という前提だった。実際は **ability も
> storage も cloud 側**（`/home/ubuntu/.../user_capabilities/<user_id>/...`, §M3.0）にあり、
> **PC から DevKit へ push しても cloud の storage には届かない**。よって本 fallback は
> **現状の本番経路としては無効**（§4.3(B) 末尾の「`--target` が storage 実体に一致するか」という
> 旧 open question も、cloud 実行の判明で「DevKit パスでは一致しない」と決着）。primary の
> public HTTPS pull（(A)）が **2026-06-26 に live で確定**したため、当面は (A) に寄せる。
> 以下の push.py 実装・記述は **transport の参考実装として残置**（local-copy transport や
> 将来 cloud 側に書き込む別経路が見つかった場合の素材）であり、本番手順ではない。

> 📎 以下は **cloud 実行の判明前**に「primary の egress が device で失敗した場合の fallback」
> （design.md §M3.3.1 の推奨順位「HTTP pull > push(scp) > 共有マウント > broker」）として設計した
> ときの記述。**現在は本番経路ではなく参考実装**（上記 🔴 を参照）。当時の設計意図として残す。

当時の設計では push を「**正式 fallback**」と位置づけた。**ability 側は一切ネットワーク
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

### 4.4 既存 capability を pull 版へ更新する（capability_id=6393 想定）

pull primary 実装（`feat/openhome-pull-primary`）を、既に作成済み・山彦(590628)へ install/enable
済みの capability **6393** へ反映する手順。**新 capability を作り直さず in-place で新バージョン化**する
（install/enable と personality 紐付けを維持）。

> ⚠️ **§6 の日常運用とは別手順**: §6 Startup Runbook の通り、**tunnel URL を埋め直すたびに
> `add-capability` で新名（`approvalvoice<N>`）で再アップロード**するのが live で確立した運用
> （cache 衝突回避のため毎回 install/enable し直す）。本 §4.4 の in-place `edit-capability` は
> **URL 不変のままバンドル中身だけ差し替えたい**ケース向けの代替で、tunnel URL ローテーションの
> 日常運用では §6 を使う。

> ⚠️ **認証の差（実測ベース）**: `add-capability` は **X-API-KEY** で通った（HTTP 201, 6393 作成）。
> 一方 **`edit-capability` / `get-all-capabilities` / `enable/release` は JWT Bearer 必須**の実測報告が
> ある（先行 worker: X-API-KEY で 401）。**まず X-API-KEY を試し、401 なら**ダッシュボード
> （app.openhome.com）DevTools → Network の `Authorization: Bearer <token>`（短命）を inline で使う。
> worker は外部 POST 不可（classifier）なので、以下は **user/窓口が `!` 経由で実行**する。

```bash
# zip は本 worktree の dist/approval-voice-ability.zip（pull 版で再ビルド済）。
export AUTH='Authorization: Bearer <DevToolsのBearer>'   # 401 時。X-API-KEY 可ならそちらでも可
ZIP=/c/Users/iwama/Documents/work/org/workers/openhome-approval-voice/.worktrees/openhome-push-transport/dist/approval-voice-ability.zip

# STEP U: 新バージョン作成（非破壊 PUT）。成功=HTTP 200。400=sandbox 新ルール（本文 token を sandbox_lint へ）
curl -sS -w '\n[HTTP %{http_code}]\n' -X PUT "https://app.openhome.com/api/capabilities/edit-capability/6393/" \
  -H "$AUTH" -F "name=approvalvoice" -F "category=background_daemon" -F "zip_file=@${ZIP}"

# STEP V: 反映確認。6393 の capability_versions で id 最大(最新版)の is_user_enabled を見る
curl -sS "https://app.openhome.com/api/capabilities/get-all-capabilities/" -H "$AUTH"

# STEP W: 最新版が is_user_enabled=false のときだけ（version 単位 enable, body 不要）
curl -sS -w '\n[HTTP %{http_code}]\n' -X POST "https://app.openhome.com/api/capabilities/enable/release/<最新version_id>/" -H "$AUTH"
# 404/405 なら代替: PUT edit-installed-capability/6393/ -F "enabled=true" -F "category=background_daemon"
```

### 4.5 live 統合の確証（pull 版・real awaiting_user gate）

1. **PC**: `py -3 -m pc_exporter serve --db-path <claude-org>/.state/state.db --since <起動時刻のUTC ...Z> --port 80`
   が live state.db を `http://localhost:80/announce_queue.json` で配信中。これを **cloudflared
   quick tunnel で public HTTPS 公開**（§6.2）し、発行された `https://<random>.trycloudflare.com`
   を `PULL_URL` に埋めて ability をビルド・再アップロード（§6.3）。`--since` で歴代 `notify_sent`
   を切り捨てる（§6.1）。
2. **real gate を 1 件 emit**（組織側、例）: `awaiting_user` イベントを state.db に記録 → exporter が即時公開。
3. **DevKit セッション再起動**: 山彦のセッションを（再）開始 → **cloud 上の daemon** 起動 →
   毎 tick `requests.get(PULL_URL)` で pull。
4. **確証**（音声＋ログ。ログは "Open In Editor" → log タブだが **cloud test env の表示**なので
   実機到達は**音声**で確認する。§5.6）:
   - 音声: 「approvalvoice デーモンが起動しました。…」→「PCとの接続に成功しました。N件…」が聞こえる＝**egress 成立**。
   - ログ: `[ApprovalVoice] pull tick=1: GET 200 (Nchars, M items) -> wrote QUEUE_STORE`。
   - 続けて `poll tick: ... fresh=M` → `read_aloud: speak ...`（**non-SMOKE な real gate を逐語**）。
   - 失敗音声（§5.6 の 6 状態）が出るなら原因切り分けへ。

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

### 5.6 TTS 6 状態の切り分け（聞こえる発話 → 原因）

ability は **cloud 上で実行**されるため、Dashboard の editor log は **OpenHome の cloud test env**
の出力で、**実機（あなたの DevKit）の到達状況とは別物**。よって実機の状態は **DevKit から
聞こえる発話そのもの**で切り分けるのが一次情報になる（PR #17/#18/#19 で実装した self-diagnostics
発話）。聞こえる音声を上から照合する:

| 聞こえる発話 | 状態 | 原因 / 次の手 |
|---|---|---|
| **完全無音**（起動発話すら出ない） | daemon 未起動、または audio 出力不通 | capability が agent に **install + enable** 済みか・**セッションを開始したか**を確認。他 ability の通常会話で音が出るなら audio device は健全（§5.5.1） |
| **「approvalvoice デーモンが起動しました。PC との接続を確認中です。」のみ** | daemon は起動したが pull がまだ成功も明確な失敗もしていない | 初回 tick 待ち（最大 `POLL_SECONDS`≒15s）。それでも続くなら次のいずれかの失敗発話が出るまで待つ／PULL_URL・tunnel・exporter を確認 |
| 起動発話 ＋ **「PCとの接続に成功しました。N件の通知が取得できました。」** | **正常**（pull egress 成立・取得成功） | 続けて real gate を逐語読み上げ。これが live 統合の確証 |
| 起動発話 ＋ **「PCに接続できません。ネットワーク経路を確認してください。」** | **接続失敗**（timeout / 接続拒否 / 名前解決失敗 = connect カテゴリ） | tunnel が起動中か（§6.2）、`PULL_URL` が**現在の** trycloudflare URL と一致するか（再起動で変わる §6.3）、exporter `serve` 稼働中か |
| 起動発話 ＋ **「PCのexporterからHTTP N が返りました。」** | **非200**（TCP/TLS は到達したが 404/500 等） | tunnel は通っている。URL の path（`/announce_queue.json`）と exporter の serve 状態を確認 |
| 起動発話 ＋ **「PCからの応答形式が想定外です。」** | **200 だが §1.3 JSON として parse 失敗** | exporter が正しい queue JSON を配信しているか。tunnel が HTML エラーページ（502 等）を本文返ししていないか |

> ℹ️ 上記以外に、connect 以外の予期しない transport エラーでは
> **「pull で予期しないエラーが発生しました。」**（request カテゴリ）が出る。発話は各カテゴリ
> **セッション中 1 回だけ**（再 arm はセッション再起動）で、ログを見られない実機での到達確認を
> 音声で代替するためのもの。

---

## 6. Startup Runbook（日常運用）

cloud 実行 + cloudflared quick tunnel 構成での**毎回の起動手順**。tunnel URL は cloudflared
プロセス停止で消え、**再起動のたびに変わる**ため、URL が変わるたびに ability を再ビルド・
再アップロードする「URL 更新サイクル」が日常運用のペインになる。以下を順に回す。

### 6.1 PC exporter を起動（`--since` で歴代キューを切り捨て）

```bash
# state.db は append-only で notify_sent イベントが累積する。--since を付けないと、
# 再 install のたびに歴代キュー全件が再読みされ全部読み上げられてしまう。起動時刻の
# UTC ISO8601(...Z) を渡し、それ以降に発生した gate だけを公開する。
py -3 -m pc_exporter serve \
    --db-path <claude-org>/.state/state.db \
    --since 2026-06-26T00:00:00Z \
    --port 80
# → "serving ... on http://0.0.0.0:80/announce_queue.json (re-export 2.0s)"
```

> ⚠️ **`--since` は `occurred_at` と同じ UTC `...Z` 形式で渡す**。フィルタは
> `occurred_at >= ?` の **辞書順（文字列）比較**で、DB の `occurred_at` は UTC + `Z` 接尾辞
> （例 `2026-06-26T01:00:00.000Z`）で格納される。**ローカル時刻（例 JST）の文字列を渡すと
> UTC 値より先行**し、新しく emit された gate が「UTC が追いつくまで」除外される事故になる。
> 必ず **UTC で現在時刻**を生成する（例 PowerShell: `(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")`、
> bash: `date -u +%Y-%m-%dT%H:%M:%SZ`）。リテラル `now` は不可（ISO8601 文字列のみ）。
> 省略すると**全歴代**が対象になる（first-run replay を招く）。`--port 80` は cloudflared を
> `http://localhost:80` に向ける都合（§6.2）。

### 6.2 cloudflared quick tunnel を起動（public HTTPS 発行）

```bash
# 初回のみ: scoop で cloudflared を導入
scoop install cloudflared

# quick tunnel を起動（アカウント不要）。localhost:80 を public HTTPS で露出する
cloudflared tunnel --url http://localhost:80
# → 標準出力に https://<random>.trycloudflare.com が発行される（これを控える）
```

> ℹ️ **quick URL は揮発的**: cloudflared プロセスを止めると URL は消え、**再起動のたびに別の
> ランダム URL** になる。永続 URL が欲しい場合は Cloudflare アカウントで **named tunnel** という
> 別オプションがある（本書はスコープ外。概要のみ）。

### 6.3 PULL_URL を更新して ability を再ビルド・再アップロード

新 tunnel URL が出るたびに以下を回す（**cache 衝突回避のため毎回新名でアップロード**）:

```bash
# (a) PULL_URL を新 tunnel URL に書き換え（approval_voice/storage.py の定数 1 行）
#     PULL_URL = "https://<random>.trycloudflare.com/announce_queue.json"

# (b) 新名（approvalvoice<N>）でラップして zip 化（N は毎回インクリメント）
py -3 deploy/build_zip.py --root-folder approvalvoice<N> --out dist/approval-voice-ability.zip

# (c) add-capability で新名アップロード（同名だと cache 衝突するため毎回 name を変える）
curl -sS -X POST "https://app.openhome.com/api/capabilities/add-capability/" \
  -H "X-API-KEY: $OPENHOME_API_KEY" \
  -F "name=approvalvoice<N>" -F "category=background_daemon" \
  -F "description=Secretary の承認待ちを逐語で読み上げる一方向 ability" \
  -F "trigger_words=承認読み上げ, approval voice" \
  -F "zip_file=@dist/approval-voice-ability.zip"

# (d) ダッシュボードで対象 agent（山彦）へ install + enable
# (e) DevKit セッションを再起動（= daemon 再起動。cloud 側 ability が新 PULL_URL で起動）
```

> ℹ️ `--root-folder` のラップ名（パッケージ名）は相対 import に対して名前非依存なので、
> `approvalvoice<N>` のように毎回変えても import は壊れない（`docs/design.md` §M3.1-sandbox）。
> URL 不変でバンドルだけ差し替えたい場合の in-place 更新は §4.4 を参照。

### 6.4 セッション再起動チェックリスト

- [ ] `pc_exporter serve` が **新しい `--since`**（今回の起動時刻）で稼働している。
- [ ] `cloudflared tunnel --url http://localhost:80` が稼働し、**新 URL を控えた**。
- [ ] `approval_voice/storage.py` の `PULL_URL` が**その新 URL**に一致している。
- [ ] `build_zip.py --root-folder approvalvoice<N>` で **N をインクリメント**して再ビルドした。
- [ ] `add-capability` を**新名**でアップロードし、agent に install + enable した。
- [ ] DevKit セッションを再起動し、**「PCとの接続に成功しました。」が聞こえる**（§5.6）。

---

## 7. 既知の「要検証」（スモークの障害にしない）

- **本番（live org state → ability storage）の配送 — 2026-06-26 に primary が確定**:
  storage-name モデルでは ability は自分の `capability_worker` storage しか読めない。PC 上で
  発生する live `awaiting_user` state をその storage へどう届けるかは **(A) primary = requests
  HTTP pull**（**cloud 上の** ability が `requests` で public HTTPS を GET → `write_file`）で
  **決着**: 2026-06-26 の live 統合で **cloud → cloudflared tunnel の GET egress が成立**し
  real gate を逐語読み上げできた（旧 ≈要検証①「device 上で egress できるか」は、実行場所が
  DevKit ではなく **cloud** と判明した上で **egress 成立を実測**＝**解消**）。
- **解消済みだった旧 open question**: ② **push の `--target` が ability storage 実体に一致するか**
  は、storage が **cloud 側**にあると判明したため「**PC からの DevKit パス push では一致しない**」と
  決着（§4.3(B)）。push fallback は本番経路としては無効・参考実装として残置。
- **残る要検証 / 運用上の注意**: ① **quick tunnel URL の揮発性**（再起動毎に変わる → §6 の URL
  更新サイクルが必要。永続化は named tunnel で別途）。② cloud test env の editor log は実機到達と
  別物なので、実機確認は §5.6 の**発話**で行う。
- self-seed スモーク（`SMOKE_AUTOSEED=True`）は egress / 配送非依存で端末内完結するため、配送検証前
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
