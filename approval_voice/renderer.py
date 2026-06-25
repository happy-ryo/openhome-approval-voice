"""4-gate speech-text generators (design.md §2).

Pure functions: AnnounceItem -> spoken string. Deterministic so unit tests can
pin the exact wording per gate. The Ability swaps *only* this generator when
adapting the shared skeleton to a different message family (design.md §5).

Design rules enforced here (§2):
- always announce the gate kind first ("承認待ちです" alone is ambiguous),
- enumerate options as numbered choices ("1 …、2 …"),
- close every readout with the one-way reminder so the listener knows the
  reply goes through the terminal, not the voice channel.
"""

from .schema import (
    GATE_CI_MERGE,
    GATE_ESCALATION,
    GATE_REPLY_RELAY,
    GATE_WORKER_COMPLETE,
    AnnounceItem,
)

# One-way contract made audible: the readout never invites a spoken answer.
ONE_WAY_SUFFIX = "返事は端末でお願いします。"


def _enumerate_options(options: list[str]) -> str:
    """["承認", "差し戻し"] -> "1 承認、2 差し戻し". Empty -> ""."""
    return "、".join(f"{i} {opt}" for i, opt in enumerate(options, start=1))


def _options_clause(options: list[str]) -> str:
    enumerated = _enumerate_options(options)
    return f"選択肢は、{enumerated}。" if enumerated else ""


def _render_worker_complete(item: AnnounceItem) -> str:
    return (
        f"ワーカー完了の承認待ちです。{item.subject} が作業完了を報告しました。"
        f"承認すると次の工程へ進みます。"
        f"{_options_clause(item.options)}{ONE_WAY_SUFFIX}"
    )


def _render_ci_merge(item: AnnounceItem) -> str:
    return (
        f"マージ承認待ちです。{item.subject} の CI がグリーンになりました。"
        f"マージしてよいか確認をお願いします。"
        f"{_options_clause(item.options)}{ONE_WAY_SUFFIX}"
    )


def _render_escalation(item: AnnounceItem) -> str:
    return (
        f"エスカレーションです。{item.subject} で判断を仰いでいます。"
        f"内容は『{item.question}』。"
        f"{_options_clause(item.options)}{ONE_WAY_SUFFIX}"
    )


def _render_reply_relay(item: AnnounceItem) -> str:
    return (
        f"転送された返答待ちです。{item.subject} から確認事項が届いています。"
        f"内容は『{item.question}』。あなたの返事を待っています。"
        f"{ONE_WAY_SUFFIX}"
    )


_RENDERERS = {
    GATE_WORKER_COMPLETE: _render_worker_complete,
    GATE_CI_MERGE: _render_ci_merge,
    GATE_ESCALATION: _render_escalation,
    GATE_REPLY_RELAY: _render_reply_relay,
}


def render_speech(item: AnnounceItem) -> str:
    """Render the spoken string for one announce item, dispatching on gate."""
    renderer = _RENDERERS.get(item.gate)
    if renderer is None:  # pragma: no cover — AnnounceItem already validates gate
        raise ValueError(f"no renderer for gate {item.gate!r}")
    return renderer(item)
