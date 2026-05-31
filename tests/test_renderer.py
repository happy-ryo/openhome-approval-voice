"""Pin the exact spoken wording for each of the 4 gates (design.md §2).

These golden strings are the contract: any change to a readout is a deliberate,
reviewed change, not an accident.
"""

from approval_voice.renderer import ONE_WAY_SUFFIX, render_speech
from approval_voice.schema import AnnounceItem


def test_worker_complete_wording():
    item = AnnounceItem(
        id="q-0001",
        gate="worker_complete",
        title="ワーカー完了の承認待ち",
        question="ワーカーが作業完了を報告しました。承認しますか。",
        subject="ログイン画面のリファクタリング",
        options=["承認", "差し戻し"],
    )
    assert render_speech(item) == (
        "ワーカー完了の承認待ちです。ログイン画面のリファクタリング が作業完了を報告しました。"
        "承認すると次の工程へ進みます。選択肢は、1 承認、2 差し戻し。返事は端末でお願いします。"
    )


def test_ci_merge_wording():
    item = AnnounceItem(
        id="q-0002",
        gate="ci_merge",
        title="マージ承認待ち",
        question="CI がグリーンになりました。マージしてよいですか。",
        subject="決済モジュールの改修",
        options=["マージ", "保留"],
    )
    assert render_speech(item) == (
        "マージ承認待ちです。決済モジュールの改修 の CI がグリーンになりました。"
        "マージしてよいか確認をお願いします。選択肢は、1 マージ、2 保留。返事は端末でお願いします。"
    )


def test_escalation_wording():
    item = AnnounceItem(
        id="q-0003",
        gate="escalation",
        title="エスカレーション",
        question="外部 API の仕様変更にどう追随するか方針を決めたい",
        subject="通知基盤の刷新",
        options=["方針A で進める", "方針B で進める", "保留して再検討"],
    )
    assert render_speech(item) == (
        "エスカレーションです。通知基盤の刷新 で判断を仰いでいます。"
        "内容は『外部 API の仕様変更にどう追随するか方針を決めたい』。"
        "選択肢は、1 方針A で進める、2 方針B で進める、3 保留して再検討。返事は端末でお願いします。"
    )


def test_reply_relay_wording():
    item = AnnounceItem(
        id="q-0004",
        gate="reply_relay",
        title="返答転送待ち",
        question="デザインレビューの指摘点を確認してほしい",
        subject="デザイナー",
        options=[],
    )
    assert render_speech(item) == (
        "転送された返答待ちです。デザイナー から確認事項が届いています。"
        "内容は『デザインレビューの指摘点を確認してほしい』。あなたの返事を待っています。"
        "返事は端末でお願いします。"
    )


def test_every_readout_is_one_way():
    # No matter the gate, the readout ends by sending the reply to the terminal.
    for gate, opts in [
        ("worker_complete", ["承認", "差し戻し"]),
        ("ci_merge", ["マージ", "保留"]),
        ("escalation", ["A", "B"]),
        ("reply_relay", []),
    ]:
        item = AnnounceItem(id="x", gate=gate, title="t", question="q", subject="s", options=opts)
        assert render_speech(item).endswith(ONE_WAY_SUFFIX)
