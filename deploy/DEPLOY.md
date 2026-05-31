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

### 7.7 残ブロッカー（推測で代替不可・窓口へ判断仰ぎ済み）
1. **`OPENHOME_JWT`（セッショントークン）未提供** → cloud のデプロイ連鎖（add-capability→install→enable）が全て 401。
   入手法: ダッシュボード（app.openhome.com）ログイン後、DevTools › Network の任意 XHR の
   `Authorization: Bearer <token>` の **Bearer 以降**を採取。鍵同様 inline・非永続で worker に渡せば worker が REST 駆動可。
   ※ AccessToken は短命（数分〜）。引き渡し直前に取得し、可能なら refresh/長命トークン有無も確認。
2. **DevKit の SSH 資格情報未提供** → device 上での queue 配置（`seed_queue.py`＋`APPROVAL_VOICE_QUEUE`）と
   ability ログ採取ができない。SSH ユーザ名＋鍵/パスワードの提供、または依頼者が DevKit 端末上で直接操作。

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
