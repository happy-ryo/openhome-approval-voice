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
   （生成後、展開→別プロセスで実機 ability の import を自動検証）
2. **アップロード**: `npx openhome-cli` か REST `add-capability`（§2）
3. **対象エージェントへ install/enable**（§3。agent id は §3.1 で調べる）
4. **キュー配置**: DevKit 上で `APPROVAL_VOICE_QUEUE=<file> py -3 deploy/seed_queue.py`（§4）
5. **bridge/ability 起動 → トリガ**（§5）
6. **成功条件を確認**: 逐語で聞こえる／自発話が再転写されない（§5.3）

---

## 1. デプロイ zip をビルドする（開発 PC）

```bash
# 既定: dist/approval-voice-ability.zip を生成し、展開→import を自動検証
py -3 deploy/build_zip.py
```

ビルドは **単一の真実源** を守る: 実機 ability `openhome_ability/`（main.py /
background.py / __init__.py / requirements.txt）を zip ルートへ、純ロジック
`approval_voice/` をその直下へ**そのまま同梱**するだけで、重複実装は作らない。

### zip レイアウト（既定 = ルート直置き）

```
approval-voice-ability.zip
├── main.py            ← interactive entry（status 読み上げ）
├── background.py      ← always-on watcher（キュー polling→逐語 speak）
├── __init__.py
├── requirements.txt   ← stdlib only（追加依存なし）
└── approval_voice/    ← background.py が import する純ロジック（同梱必須）
    ├── __init__.py
    ├── schema.py  renderer.py  poller.py  bridge.py  ability.py  speak.py
```

> ⚠️ **レイアウトの肝**: `background.py` は起動時に
> `sys.path.insert(0, os.path.dirname(__file__))` してから `from approval_voice ...`
> する。よって `approval_voice/` が `background.py` と**同じ階層**に無いと import が
> 壊れる。これが過去に指摘された最大の注意点。

### import 健全性の事前検証（自動）

`build_zip.py` は zip を temp へ展開し、**別プロセス**で実機 ability を import する:

- `background.py` / `main.py` は OpenHome ランタイム `src.*` を import するが、それは
  本リポに無い。そこで temp に **最小スタブ `src`** を置き（`approval_voice` は
  同梱の本物を解決させる）、cwd=展開先で `import background` / `import main` を実行。
  → 「展開後レイアウトで `from approval_voice...` が解決する」＝import/パス不一致が
  無いことを実証する。
- 続けて `examples/announce_queue.json`(4 ゲート) を
  `load_queue → ReadCursor → render_speech` に流し、4 件レンダリングを確認。

成功すると `[verify] ability import + data path OK (VERIFY_OK 4)` が出る。

> ⚠️ OpenHome 側が**ラップフォルダ付き**（`<name>/main.py ...`）の zip を要求して
> アップロードが弾かれた場合は、保険のフォルダ版で作り直す:
>
> ```bash
> py -3 deploy/build_zip.py --root-folder approval-voice
> ```

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
**`category=background`**、`zip_file` にビルド済み zip を載せる。
`name` / `category` / `description` / `trigger_words` / `zip_file` が必須。

```bash
export OPENHOME_API_KEY="<伏字: あなたのAPIキー>"

curl -sS -X POST \
  "https://app.openhome.com/api/capabilities/add-capability/" \
  -H "X-API-KEY: $OPENHOME_API_KEY" \
  -F "name=approval-voice" \
  -F "category=background" \
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
> 実際のメソッド/パス/ボディに合わせること。`category=background` の capability は
> 追加後に enable（有効化）が必要な場合がある。`npx openhome-cli --help` の
> install 系サブコマンドでも可。

---

## 4. サンプルキューを DevKit に配置

スモークでは本番の PC→DevKit transport は使わず、**DevKit ローカルのキュー**でよい。
canonical なサンプルは `examples/announce_queue.json`（4 ゲート=worker_complete /
ci_merge / escalation / reply_relay を 1 件ずつ、§1.3 スキーマ準拠）。

### 4.1 キューパスの解決（最重要: open(QUEUE_PATH)）

ability(`background.py`) は **単一の JSON ファイル**（中身は §1.3 アイテムの配列）を
次の優先順で解決する:

1. 環境変数 `APPROVAL_VOICE_QUEUE`（**ファイルパス**。推奨: ここで明示）
2. （未設定時）既定 `~/.openhome/approval_voice/announce_queue.json`

既読カーソルは**別ファイル** `APPROVAL_VOICE_SEEN`（既定
`~/.openhome/approval_voice/announce_seen.json`）に ability 側ローカルで永続。
poll 間隔は `APPROVAL_VOICE_POLL_SECONDS`（既定 15 秒）。

> ⚠️ **詰まりやすいポイント**: `APPROVAL_VOICE_QUEUE` は**ディレクトリではなくファイル**。
> ability の既定は `~` 展開（=ability プロセスのホーム）。OpenHome ランタイムが
> ability をどの cwd/ホームで起動するかは不定なので、**`APPROVAL_VOICE_QUEUE` を
> 絶対ファイルパスで設定**し、seed_queue も同じ絶対パスへ配置すること。これで
> 「キューに入れたのに ability が見つけられない」ズレを防ぐ。
> ※ キューは ability が**読み取り専用**で扱い、処理後は queue を書き換えず
> `APPROVAL_VOICE_SEEN` に既読 id を足すだけ（副作用ゼロ）。再読み上げを試すには
> seen ファイルを消すか別パスにする。

### 4.2 配置（seed_queue ヘルパ）

```bash
# DevKit 上で実行。絶対ファイルパスを 1 か所で決め、ability と共有する
export APPROVAL_VOICE_QUEUE="/data/approval_voice/announce_queue.json"   # 例。書き込み可な絶対パス

py -3 deploy/seed_queue.py
# → seeded 4 gate(s) into queue file: /data/approval_voice/announce_queue.json

# 1 件だけ試すとき
py -3 deploy/seed_queue.py --first-only
```

seed_queue は committed 原本(`examples/announce_queue.json`)を**コピー**する
（原本を汚さない・単一の真実源）。

---

## 5. DevKit 実機チェックリスト

### 5.1 起動

```bash
# キューパスは §4 と同一の絶対ファイルパスを使う（最重要）
export APPROVAL_VOICE_QUEUE="/data/approval_voice/announce_queue.json"
export APPROVAL_VOICE_SEEN="/data/approval_voice/announce_seen.json"
```

OpenHome ランタイム経由で、§3 で enable した capability の **background ability
(`background.py` の `ApprovalVoiceWatcher`)** が常駐起動し、`APPROVAL_VOICE_QUEUE`
を `APPROVAL_VOICE_POLL_SECONDS` 秒ごとに polling する。env はその ability
プロセスから見える形で設定すること（エージェント/ability の環境設定に依存）。

### 5.2 トリガ

- background ability が起動していれば、キューに未読アイテムが現れた次の poll で
  自動的に読み上げる（手動トリガ不要。これが background category の役割）。
- interactive 側（`main.py` の `ApprovalVoiceStatus`）を trigger words で起動すると
  「承認音声リーダーは常駐で動作しています…」と status を 1 行喋る＝**導通確認**。
- キュー配置 → 最大 `POLL_SECONDS` 秒で 4 ゲートを順に読み上げ。

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

- 何も聞こえない: §4.1 のキューパス不一致が筆頭。`APPROVAL_VOICE_QUEUE` が
  ability プロセスと seed_queue で**同一絶対ファイルパス**か確認。
- import エラー: zip レイアウト不一致。§1 の `--root-folder` 版で作り直して再アップロード。
- 1 回読んだ後 2 回目が鳴らない: 正常（seen で dedup 済み）。再試行は
  `APPROVAL_VOICE_SEEN` を消すか別パスにしてから seed。

---

## 6. 既知の「要検証」（スモークの障害にしない）

- **on-device ability の outbound HTTP 可否**: 本番の PC→DevKit 同一LAN transport
  （HTTP pull, `docs/design.md` §M3.3.1）では ability から外向き通信が必要に
  なりうる。これは**本番連携の段で確認**する事項であり、本スモーク
  （DevKit ローカルキューで完結）のブロッカーにはしない。

---

## 7. ライブ調査で判明した実値・実エンドポイント（Refs #7・本デプロイ実行時に実測）

> 鍵・JWT・グローバル IPv6 などの機微値は本書では**伏字**。実値は完了報告（peer message）で窓口にのみ伝える。

### 7.1 API ベース／スキーマの実在ソース
- OpenAPI 2.0 スキーマ（権威ソース）: `GET https://app.openhome.com/api/swagger/?format=.json`
  （`/swagger.json` や `?format=openapi` は UI HTML/406 を返す。`?format=.json` が JSON 実体）。
- 当て推量パスは Django の 404 デバッグページ（`DEBUG=True`）が urlconf を露出するため、
  プレフィックス一覧（`api/capabilities/` `api/personalities/` `api/devkit/` 等）を確認できる。

### 7.2 認証モデル（最重要・実測で判明）
- **2 系統が混在**。`X-API-KEY` ヘッダで通るのは**読み取りの一部のみ**:
  - ✅ `GET /api/accounts/get-user/`（200。ユーザ id=93883 / Google サインイン・`has_password:false`）
  - ✅ `GET /api/personalities/get-all-personalities/`（200。= 旧称「agents」）
  - ✅ `GET /api/capabilities/get-installed-capabilities/`（200）
- それ以外（**add-capability / install / enable / get-all-capabilities / get/categories / devkit/get-devices** 等）は
  `Authorization: Bearer <JWT AccessToken>`（rest_framework_simplejwt）が必須。`X-API-KEY` では **401**。
  - 実証: `OPTIONS` と実 `POST` の両方で `add-capability` → `401 Authentication credentials were not provided`（=何も作成されない）。
  - `Authorization: Bearer <API_KEY>` は `token_not_valid`（API キーは JWT ではない）。
- `has_password:false`（Google OAuth）のため `/accounts/login/` で JWT を発行できない。
- 公式 `openhome-cli`（npm `openhome-cli@0.1.40`）の README も **`OPENHOME_API_KEY` と `OPENHOME_JWT` の両方**を要求
  （`openhome deploy <zip> --name --category --triggers` / `openhome assign --agent --capabilities`）。
  ⇒ **デプロイ系には JWT セッショントークンが必須**。API キー単独では不可（要検証ではなく実測確定）。

### 7.3 実エンドポイント（正しいパス）
| 操作 | メソッド・パス | 認証 |
|---|---|---|
| エージェント一覧（=personalities） | `GET /api/personalities/get-all-personalities/` | X-API-KEY 可 |
| capability アップロード | `POST /api/capabilities/add-capability/`（multipart: `zip_file,name,category,description,trigger_words,template,selected_keys,image_file`） | **JWT 必須** |
| capability 一覧 | `GET /api/capabilities/get-all-capabilities/` | **JWT 必須** |
| カテゴリ一覧 | `GET /api/capabilities/get/categories/` | **JWT 必須** |
| install | `GET /api/capabilities/install-capability/{capability_id}/` | **JWT 必須** |
| release 有効化 | `POST /api/capabilities/enable/release/{release_id}/` | **JWT 必須** |
| release 一覧 | `GET /api/capabilities/list/capability-releases/{capability_id}/` | **JWT 必須** |
| agent の capability | `GET /api/capabilities/get/agent-capabilities/{user_id}/` | **JWT 必須** |
| DevKit デバイス一覧 | `GET /api/devkit/get-devices/` | **JWT 必須** |

> ⚠️ DEPLOY.md §2/§3 の旧記載（`get-all-agents/` パス・`X-API-KEY` のみでアップロード可）は**実測で否定**。
> 正は本節。`/api/agents/...` というプレフィックスは存在せず、エージェント＝`personalities`。

### 7.4 専用エージェント（特定済み）
- **山彦（Yamabiko）`id=590628`**＝逐語読み上げ専用エージェント（description「ユーザの発話を一字一句そのまま読み上げる…要約・言い換え・追加…」）。
- approval-voice capability は**未アップロード／未インストール**（installed は OpenHome 既定 6 件のみ）。重複なし。

### 7.5 `category` 値の要検証
- DEPLOY.md §2 は `category=background`。だが installed caps の実カテゴリ値は
  `background_daemon` / `skill` / `brain_skill`。⇒ **`category=background` は要検証**。
  `get/categories/`（JWT 必須）で正値を読んでから upload すること（JWT 入手後に確定）。

### 7.6 DevKit 到達手段（LAN・read-only で実測）
- **mDNS `openhome.local` で到達可**（ping 応答 2–5ms / 0% loss）。`raspberrypi.local` は不在。
  （実 IPv6 アドレスは公開リポ衛生のため**伏字**。完了報告で窓口に伝達。）
- **SSH(22) OPEN**: バナー `SSH-2.0-OpenSSH_10.0p2 Debian-7+deb13u2`（Debian 13 ベース＝Pi DevKit イメージと整合）。
- HTTP 80/8080/443 は応答なし（DevKit 上に Web サービスは無し）。
- ⇒ 到達は可能だが **SSH ログイン資格情報（ユーザ名＋鍵/パスワード）は未提供**。
  認証ブルートフォース・既定資格情報の当て推量は**禁止につき試行せず**（要 escalation）。

### 7.5b デプロイ実行ログ（JWT 受領後・実測で確定した packaging 規則）
JWT（access, exp≈7日）受領後に add-capability を実行し、以下を実測で確定:
- **認証**: `Authorization: Bearer <JWT>` で capabilities API 全面が通る（categories/get-all/add 全て 200/処理到達）。
- **category 正値**: `get/categories/` の実値は `skill`/`brain_skill`/**`background_daemon`**（"Runs in the background always"）/`local`（"Runs on DevKit Hardware"）。
  ⇒ DEPLOY.md 旧 `category=background` は誤り。常駐 watcher は **`category=background_daemon`** が正。
- **name 規則**: 英字始まり・英数字のみ（ハイフン不可）。`approval-voice`→400。**`approvalvoice`** で通過。
- **zip レイアウト**: ルート直置きは `main.py file not found` で 400。**ラップフォルダ必須**＝`build_zip.py --root-folder approvalvoice`（`approvalvoice/main.py ...`）。
- **重複なし**: `get-all-capabilities` = 0 件（本実行で capability は一切作成されていない＝全て 400/未作成）。

### 7.5c ⛔ Ability sandbox がコア設計と非互換（最重要・要設計判断）
add-capability はアップロード zip を**静的スキャン**し、禁止 import/パターンがあると 400。実測＋公式
[SDK Reference](https://docs.openhome.com/api-sdk/sdk-reference.md) で確定した禁止事項:
- **`import sys`**（実測 400: `Forbidden import of module 'sys'` @background.py）
- **`import os`**（top-level 不可）
- **top-level `import json`**（register ブロック外で不可）
- **`signal`**、`redis`/`connection_manager`/`user_config`、`exec()`/`eval()`/`pickle`
- **raw `open()` 不可** → `capability_worker` のファイルヘルパを使う:
  `read_file(name, temp)` / `write_file(name, content, temp)` / `check_if_file_exists(name, temp)` / `delete_file()`
  （ストレージは**パスではなく名前**で扱う。JSON 永続は append 不可＝delete+write）

> ⚠️ 現行 approval-voice は **`background.py`＋`approval_voice/bridge.py`＋`approval_voice/poller.py`** が
> `os`（path/environ/expanduser/exists/replace）・top-level `json`・raw `open()`・`pathlib.Path`・`sys.path.insert`
> に**全面依存**。これは sandbox の禁止セットそのもの。⇒ **localized な import 修正では通らず、
> 「ローカル JSON ファイルを open() で読む」というトランスポート設計の作り替えが必要**。
> （design.md §M3 で "appliance sandbox 制約は未確認/要検証" としていた点が、本実行で**否定的に確定**）。
> `import sys` のみ除去した最小候補（相対/絶対 import・local import 検証 OK）は用意済みだが、
> `os`/`json`/`open()` が残るため通らない見込み（次の 400 を取る確認アップロードは harness の
> 本番デプロイ保護により要・明示承認）。**ability 再設計はこの deploy 実行タスク（chore/minimal）の
> 範囲を超える**ため、別タスク化/スコープ判断を窓口へ判断仰ぎ。

### 7.6b ✅ B（SSH 無し経路）の答え＝OpenHome 管理ストレージ
sandbox が raw `open()` を禁じ `capability_worker.read_file/write_file` を強制する事実は、そのまま
**「SSH でローカルにキューファイルを置く」必要が無い**ことを意味する。SDK Reference の協調パターン:
> "Main writes data to persistent file storage. Background polls that file on a timer and acts on it."
⇒ 再設計後は **interactive(main.py) が `write_file` でサンプルを書き → background が `read_file` で読んで speak()**。
seed_queue.py の SSH 配置も `APPROVAL_VOICE_QUEUE` 絶対パスも不要になり、**smoke test に SSH 不要**。
（= 窓口 B 要求「SSH を知らなくても smoke に到達」の最短経路。ただし上記 7.5c の再設計が前提。）

### 7.7 残ブロッカー（推測で代替不可・窓口へ判断仰ぎ済み）
1. ~~`OPENHOME_JWT` 未提供~~ → **解決**（窓口より受領。auth 系統は全て疎通確認済み）。
2. **【最重要】Ability sandbox 非互換 → ability 再設計が必要（スコープ判断）**: §7.5c の通り、現行 ability は
   `os`/top-level `json`/raw `open()`/`sys.path` 依存で add-capability の静的スキャンを通過できない。
   `capability_worker.read_file/write_file/check_if_file_exists` ベースへトランスポートを作り替える必要があり、
   これは pure-logic（単一の真実源）と transport の設計変更＝本 deploy 実行タスク（chore/minimal）の範囲外。
   → **要判断**: (a) 別タスクで M3 ability を sandbox 準拠に再設計するか、(b) 別アプローチか。
3. **harness の本番デプロイ保護**: code 編集済み capability の add-capability アップロードは Claude Code の
   auto-mode classifier に 2 回ブロックされた（本番外部サービスへの write／編集コードの扱い）。
   再設計後の正式アップロードには、窓口/依頼者からの**明示的な実行承認**（または該当 Bash 権限の許可）が要る。
4. ~~DevKit SSH creds~~ → **smoke には不要化**（§7.6b。OpenHome 管理ストレージ経由で SSH レス。
   ただし device ログでの speak() 証跡採取を望む場合は別途 SSH か依頼者の device 操作が要る）。

---

## 8. M3 再設計（OpenHome sandbox 準拠・Refs #11）

§7.5c の sandbox 非互換を受け、人間承認のスコープ拡張（Issue #11）で ability を
**sandbox 準拠**に作り替えた。単一の真実源と一方向保証は維持。

### 8.1 方針＝「bundle は純モジュールだけ・ファイル I/O は層を分離」
- **bundle（on-device, 静的スキャン対象）= 純粋／sandbox-clean**:
  `os`/`sys`/raw `open()`/top-level `json`/`pathlib`/`signal` を一切含めない。
- ファイル I/O は2層に分離:
  - **PC/テスト側** = `approval_voice/fileio.py`（`os`/`pathlib` 可）。**bundle から除外**。
  - **on-device 側** = `openhome_ability/background.py`・`main.py` が
    `capability_worker.read_file/write_file/check_if_file_exists/delete_file`（**名前ベース**ストレージ, async）で I/O。
- シリアライズは `approval_voice/codec.py` に一本化（`json` は**関数内 lazy import**）。重複実装なし。

### 8.2 モジュール構成（bundle in/out）
| モジュール | 役割 | bundle |
|---|---|---|
| `approval_voice/schema.py` `renderer.py` | スキーマ／4ゲート描画（純） | ✅ |
| `approval_voice/poller.py` | `ReadCursor`（純 dedup のみ。file I/O は撤去） | ✅ |
| `approval_voice/bridge.py` | `notification_to_item`（公開衛生フィルタのみ。file write は撤去） | ✅ |
| `approval_voice/codec.py` | JSON⇔オブジェクト（lazy `json`。単一シリアライズ源） | ✅ |
| `approval_voice/transport.py` | ストレージ名定数＋4ゲートサンプル（`examples/announce_queue.json` と drift-guard test で一致） | ✅ |
| `approval_voice/fileio.py` | PC/テスト用の `load_queue/export_queue/load_seen/save_seen`（`os`/`pathlib`） | ❌ 除外 |
| `approval_voice/ability.py` `speak.py` | M2 モック（device 未使用） | ❌ 除外 |
| `openhome_ability/background.py` | 常駐 watcher（capability_worker で read/speak） | ✅ |
| `openhome_ability/main.py` | interactive：write_file でサンプル投入＋status 読み上げ | ✅ |

### 8.3 ローカル担保（テスト green = bundle clean）
- `deploy/build_zip.py` に **`scan_bundle_clean()`** を追加。**実際にステージされた全 .py** を
  走査し、禁止 import/`open()`/top-level `json`/`pathlib`/**dunder 属性アクセス**（`\.__\w+__`、
  例 `cls.__dataclass_fields__`）があればアップロード前に弾く（ハンドメンテ表ではなく
  実バンドルを単一ソースに）。`--root-folder approvalvoice` 版も clean を確認済。
  - ⚠️ sandbox ルールは**アップロードのたびに段階的に判明**する（import 系 → dunder 属性アクセス…）。
    `__dataclass_fields__` 依存は明示フィールド列挙（`schema._ITEM_FIELDS`）＋明示 `to_dict()` に置換し、
    `asdict`/introspection を撤去。スキャナはコメント/docstring 内のトークンも拾うため、bundled の
    プローズからも禁止トークン（literal の dunder 等）を除去している。**未知ルールが更に出る可能性は残る**
    （local scan は既知ルールのみ担保）。
- `tests/test_sandbox_clean.py`：実 zip をビルドして bundle の in/out を検証＋scanner の negative control。
- `tests/test_one_way.py`：再設計後も forbidden voice-input API 不使用を AST で担保（capability_worker の
  read_file/write_file/speak/send_interrupt_signal は input-capture ではない）。
- `tests/test_pipeline.py`：fileio へ import 先を更新＋サンプル drift-guard。**全 18 test green / VERIFY_OK 4**。

### 8.4 スモーク（SSH レス）
1. `--root-folder approvalvoice` でビルド → `category=background_daemon`・`name=approvalvoice` で add-capability
2. install-capability → enable → 山彦(`590628`) へ assign
3. interactive（main.py）をトリガ＝`write_file` で 4 ゲートを投入 → background が `read_file`→`speak()` を順次実行
4. ⇒ **SSH 不要**（OpenHome 管理ストレージ経由）。最終「逐語で聞こえたか」の試聴のみ依頼者。

### 8.5 cloud デプロイ連鎖（公式 openhome-cli ソースで確定・Bradymck/openhome-cli）
CLI `src/api/{endpoints,client}.ts` で各ステップの実 REST を確定（**install/enable/release
という旧推測は不正確**。正は下記）。全て `Authorization: Bearer <JWT>`。
1. **deploy(upload)**: `POST /api/capabilities/add-capability/`（multipart: `zip_file`,`name`,
   `description`,`category`,`trigger_words`,**任意 `personality_id`**）。
   ⇒ `personality_id=590628`（山彦）を**同梱すればアップロード時にエージェント紐付けまで一括**。返却に capability id。
2. **enable(toggle)**: `PUT /api/capabilities/edit-installed-capability/{installed_cap_id}/`
   に `{enabled: true, category, trigger_words}`（現状を get してから enabled を立てて PUT）。
3. **assign**: `PUT /api/personalities/edit-personality/` に form `personality_id=590628` ＋
   `matching_capabilities=<capId>`（複数可・繰り返し）。
- 一覧/ID 解決: `GET /api/capabilities/get-all-capabilities/`（user 作成 ability。`is_installed`,`id`）。
  エージェント一覧は CLI だと `/api/sdk/get_personalities`（本書 §7.3 の get-all-personalities でも可）。
- ⇒ worker 駆動順: add-capability(+personality_id) → get-all-capabilities で installed id 確認 →
  edit-installed-capability(enabled) →（必要なら）edit-personality で assign。各 id を報告。

> ⚠️ **on-device 実行時の前提（local では検証不可・device で要確認）**: `capability_worker` の
> 各 async ヘルパのシグネチャ／`temp=False` 永続ストレージが interactive↔daemon で共有される点／
> `def call()`+`session_tasks.create()` の daemon 形（alarm-timer 由来）。worker は SDK doc/実機 ability に
> 沿って実装したが、実音声と同様「実機で動いた」は依頼者の確認事項とする。

---

## 付録: ファイル一覧

| パス | 役割 |
|---|---|
| `deploy/build_zip.py` | デプロイ zip 生成 + 展開→実機 ability import 自動検証 |
| `deploy/seed_queue.py` | サンプルキューを DevKit キューファイルへコピー配置 |
| `examples/announce_queue.json` | canonical 4 ゲートサンプル（§1.3 準拠・test 済） |
| `openhome_ability/background.py` | on-device 常駐 watcher（`approval_voice` を import） |
| `openhome_ability/main.py` | interactive 導通確認エントリ |
| `approval_voice/` | 純ロジック（schema/renderer/poller/bridge/...）= 単一の真実源 |
