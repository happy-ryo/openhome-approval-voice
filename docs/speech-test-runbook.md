# 発話テスト ランブック（実機 on-device speech test）

「発話テストして」と依頼されたときに**そのままなぞれば DevKit から実音声を 1 回鳴らせる**手順。
2026-06-26 に実走して #7 の end-to-end を通した経路を正準化したもの。
背景・契約の詳細は [`deploy/DEPLOY.md`](../deploy/DEPLOY.md)（特に §5.6 / §6）と
[`docs/design.md`](./design.md) §M3 を一次参照する。本書は**最短の再現手順**に絞る。

---

## 0. アーキテクチャ要点（なぜこの手順なのか）

- ability コードは **OpenHome の cloud（Ubuntu サーバ）上で実行**され、DevKit は audio I/O 端末。
  → cloud から PC を pull するため **PC 側のキューは public HTTPS で露出**が必須
  （private LAN IP は cloud から不可達）。
- データパス: `awaiting_user` イベント（`state.db`）→ `pc_exporter serve`（PC, port 8731）
  → cloudflared public HTTPS トンネル → cloud daemon が `requests.get` で pull
  → DevKit が `speak()` 逐語読み上げ（一方向 GET のみ・返答キャプチャなし）。
- **トンネル URL は cloudflared 再起動のたびに変わる**。URL が変わったら ability を
  再ビルド・再アップロードする（§3 がそのサイクル）。これが日常運用の主なペイン。

### この環境の固定値（2026-06-26 実機）

| 項目 | 値 |
|---|---|
| repo（このプロジェクト） | `/home/happy_ryo/work/org/workers/openhome-approval-voice` |
| claude-org state.db | `/home/happy_ryo/work/org/claude-org-ja/.state/state.db` |
| Python（依存導入済み） | `/home/happy_ryo/work/org/claude-org-ja/.venv/bin/python`（クリーン checkout なら `pip install -e ".[dev]"` 後の `python`） |
| exporter port / route | `8731` / `/announce_queue.json`（`pc_exporter/server.py` の `DEFAULT_PORT` / `QUEUE_ROUTE`） |
| cloudflared | `~/.local/bin/cloudflared`（`cloudflared-linux-amd64`） |
| poll 間隔 | 15 秒（`approval_voice/storage.py: POLL_SECONDS`） |
| 対象 agent | 山彦（以前の personality 590628） |

> 🔑 **API キーは repo に残さない**。`OPENHOME_API_KEY` はユーザーの手元シェルで `!` 経由で渡す。
> 🔒 **public 露出の境界**: cloudflared トンネルは内部キューを公開インターネットに出す。
> Claude（窓口）側の自動承認ガードはこれを exfiltration として止めるため、**トンネル起動は
> ユーザーが `!` で実行**する（境界を越える判断はユーザーが行う）。露出されるのは
> emit した demo ゲートの文面のみ（`--since` で過去は切り捨てる）。

> 🔌 **ポートは 3 点が揃えば値は任意**: exporter の `--port`・cloudflared の
> `--url http://localhost:<port>`・`PULL_URL` のホスト側ポート、この **3 つが同一**であること
> だけが不変条件。本書は `pc_exporter/server.py` の既定 `DEFAULT_PORT=8731` をそのまま使う。
> [`deploy/DEPLOY.md`](../deploy/DEPLOY.md) §6 は `--port 80` を例示するが、80 は Linux で特権が
> 要るため本書は非特権の 8731 を採る（3 点を揃える限りどちらでも可）。

---

## 1. 最速の再テスト（依頼が来たらまずここを試す）

**前提**: 前回の exporter・cloudflared トンネル・capability が**まだ生きている**（URL 不変）。

```bash
# (a) exporter が生きているか（生きていれば JSON か [] が返る）
curl -s http://localhost:8731/announce_queue.json | head -c 200; echo

# (b) demo ゲートを 1 件 emit（claude-org 側で。note は短く＝subject に入る）
cd /home/happy_ryo/work/org/claude-org-ja
bash tools/journal_append.sh notify_sent kind=awaiting_user \
    task_id=openhome-demo gate=escalation_to_user note=OpenHome音声連携デモ

# (c) exporter に載ったか確認（1 件返ればOK）。serve は再エクスポート間隔(既定 2s)ごとに
#     キューを書き直すので、emit 直後は 1 tick 待ってから確認する（空振り回避）
sleep 3; curl -s http://localhost:8731/announce_queue.json
```

→ **DevKit セッションを再起動**して §4 の音声を聴く。これだけで鳴る。
鳴らない / 「PCに接続できません」等が出たら URL が変わっている → §2〜§3（フル）へ。

> ⚠️ **二重読み上げ防止の dedup**: `SMOKE_AUTOSEED=False`（本番）では既読カーソル
> `announce_seen.json` はセッション再起動で**リセットされない**。同じ `evt-<id>` は一度しか
> 読まれない。新しく鳴らしたいときは**新しいゲートを emit**する（毎回別 `evt-id` になる）。

---

## 2. PC 側スタックを立ち上げる（トンネルが死んでいる／初回）

```bash
# (1) exporter を起動（--since=今この瞬間UTC で過去ゲートを切り捨てる。秒精度+.000Z）
SINCE=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
DB=/home/happy_ryo/work/org/claude-org-ja/.state/state.db
PYBIN=/home/happy_ryo/work/org/claude-org-ja/.venv/bin/python
cd /home/happy_ryo/work/org/workers/openhome-approval-voice
APPROVAL_VOICE_HTTP_PORT=8731 nohup $PYBIN -m pc_exporter serve \
    --db-path "$DB" --since "$SINCE" --port 8731 > /tmp/claude/exporter.log 2>&1 &
```

```bash
# (2) cloudflared public トンネル ← ★ユーザーが ! で実行する（境界越えはユーザー判断）
! nohup ~/.local/bin/cloudflared tunnel --url http://localhost:8731 \
    > /tmp/claude/cloudflared.log 2>&1 & sleep 6 && \
    grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/claude/cloudflared.log | head -1
# → https://<random>.trycloudflare.com を控える（= 新 PULL_URL のホスト）
```

---

## 3. ability を新 URL で再ビルド & 再アップロード（URL が変わったら毎回）

```bash
# (1) PULL_URL を新トンネル URL に差し替え（approval_voice/storage.py の 1 行）
#     PULL_URL = "https://<random>.trycloudflare.com/announce_queue.json"

# (2) 本番モード確認: approval_voice/sample.py の SMOKE_AUTOSEED が False
#     （True だとサンプル4件で self-seed され、実ゲートを読まない）

# (3) 新名でラップして zip 化（cache 衝突回避で毎回 N を変える）。lint+import 検証が自動で走る
$PYBIN deploy/build_zip.py --root-folder approvalvoice<N> --out dist/approval-voice-ability.zip
#     → [sandbox] ... OK / [verify] ... VERIFY_OK 4 が出れば健全

# (4) アップロード ← ★ユーザーが ! で実行（API キーを渡す）。HTTP 201 で受理
! OPENHOME_API_KEY="<APIキー>"; curl -sS -X POST \
    "https://app.openhome.com/api/capabilities/add-capability/" \
    -H "X-API-KEY: $OPENHOME_API_KEY" \
    -F "name=approvalvoice<N>" -F "category=background_daemon" \
    -F "description=Secretary approval read-aloud (one-way)" \
    -F "trigger_words=承認読み上げ, approval voice" \
    -F "zip_file=@/home/happy_ryo/work/org/workers/openhome-approval-voice/dist/approval-voice-ability.zip" \
    -w '\n[HTTP %{http_code}]\n'

# (5) 🔴 先に旧 approvalvoice<N-1> を disable/uninstall → 新名を install + enable
#     （ダッシュボード app.openhome.com で対象 agent=山彦 の capability 一覧から操作）
#     旧 daemon を止めないと古い PULL_URL を poll し続け二重起動・二重読み上げになる
```

> `category` は **`background_daemon`**（`background` は無効値）。`name` は英数字のみ。
> ラップ名（パッケージ名）は相対 import に対し名前非依存なので毎回変えてよい。
> `description` / `trigger_words` の正本は [`deploy/DEPLOY.md`](../deploy/DEPLOY.md) §6.3。

---

## 4. 確証（実機の状態は「聞こえる発話」で判定する）

cloud の editor log は cloud test env のもので実機到達とは別物。**DevKit から聞こえる発話**が一次情報。
セッション再起動後、上から照合する（[`deploy/DEPLOY.md`](../deploy/DEPLOY.md) §5.6 が正本）:

| 聞こえる発話 | 状態 |
|---|---|
| 「approvalvoice デーモンが起動しました。PC との接続を確認中です。」（のみ続く） | daemon 起動・初回 pull 待ち（最大 `POLL_SECONDS`≒15 秒）。続くなら URL/tunnel/exporter を確認 |
| **「PCとの接続に成功しました。N件の通知が取得できました。」** | **egress 成立（live 確証）** |
| （続けて）「エスカレーションです。… で判断を仰いでいます。内容は『…』。返事は端末でお願いします。」 | **ゲート逐語読み上げ＝発話テスト成功** |
| 「PCに接続できません…」 | tunnel/exporter 不通（connect）。URL が現在のトンネルと一致するか・exporter 稼働中か |
| 「PCのexporterからHTTP N が返りました。」 | 非200。path `/announce_queue.json`・exporter を確認 |
| 「PCからの応答形式が想定外です。」 | 200 だが JSON parse 失敗。tunnel が HTML エラーページを返していないか |
| 「pull で予期しないエラーが発生しました。」 | connect/http/parse 以外の transport 例外（request カテゴリ） |
| 完全無音 | daemon 未起動 or audio。install/enable 済みか・セッションを開始したか・他 ability で音が出るか |

> ℹ️ 各失敗発話は**セッション中 1 回だけ**（再 arm はセッション再起動）。ログを見られない実機での
> 到達確認を音声で代替するためのもの。状態と次手の網羅は §5.6 が正本。

---

## 付録: ゲート種別と読み上げ文面のプレビュー

`note` は短く（PR#/Issue#/要約相当）。`note` が subject に入り、`options` は実ゲートでは常に空。
org ゲート名 → §1.3 ゲートの対応（`pc_exporter/core.py: GATE_MAP`）:

| emit する org gate | §1.3 gate | 読み上げ見出し |
|---|---|---|
| `worker_completed` | `worker_complete` | ワーカー完了の承認待ちです。… |
| `ci_green_merge_gate` | `ci_merge` | マージ承認待ちです。… |
| `escalation_to_user` / `ci_unconfirmed_head_gate` | `escalation` | エスカレーションです。… |
| `escalation_reply_forward` | `reply_relay` | 転送された返答待ちです。… |

emit 前に発話文字列をローカルでプレビューできる:

```bash
$PYBIN - <<'EOF'
from approval_voice.bridge import items_from_raw
from approval_voice.renderer import render_speech
note = "OpenHome音声連携デモ"
raw = [{"id":"evt-demo","gate":"escalation","title":"x","question":note,
        "subject":note,"options":[],"created_at":"2026-06-26T11:00:00.000Z"}]
print(render_speech(items_from_raw(raw)[0]))
EOF
```

> emit は claude-org の `state.db` に実イベントを書く（attention watcher が動いていれば
> `secretary_awaiting_user` で鳴る）。デモ用の note は無害な文言にする。
