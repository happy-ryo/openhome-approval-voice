"""Background Ability skeleton (design.md §1.2 ③④, §5-2).

Conceptual mock of the OpenHome Background (Always-On) Ability. The real
Ability subclasses `MatchingCapability`, runs a `while True` +
`session_tasks.sleep()` loop, and calls `send_interrupt_signal()` then
`speak()` (design.md §4). M2 mocks all of that and exercises the data path:

    queue -> read-cursor (dedup) -> 4-gate renderer -> speak() mock

One-way guarantee (design.md §3.1): the only output is `speak()`. This module
must never call `user_response()`, `run_io_loop()`, `run_confirmation_loop()`,
or `start_audio_recording()`. After reading aloud it returns straight to the
poll loop and never enters an interactive/confirmation state.
"""

from __future__ import annotations

from collections.abc import Callable

from .poller import ReadCursor
from .renderer import render_speech
from .schema import AnnounceItem
from .speak import speak


class ApprovalVoiceAbility:
    """Polls the announce queue and reads new approval gates aloud, once each."""

    def __init__(
        self,
        speak_fn: Callable[[str], str] = speak,
        cursor: ReadCursor | None = None,
    ) -> None:
        self._speak = speak_fn
        self._cursor = cursor or ReadCursor()

    def poll_once(self, items: list[AnnounceItem]) -> list[str]:
        """One poll tick: render + speak every unread item, return utterances.

        Mirrors one iteration of the real Background loop body, minus the sleep
        and the OpenHome interrupt call.
        """
        fresh = self._cursor.unread(items)
        spoken: list[str] = []
        for item in fresh:
            # Real Ability: send_interrupt_signal() here before speaking.
            spoken.append(self._speak(render_speech(item)))
        self._cursor.mark_read(fresh)
        return spoken
