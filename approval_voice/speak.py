"""Mocked OpenHome voice output (design.md §3.1, §4).

In M2 there is no OpenHome connection. `speak()` stands in for
`OpenHome.speak()` / `text_to_speech()` and simply emits the string it *would*
have spoken to the log / stdout, so the PoC can be verified locally.

One-way guarantee (design.md §3.1): this module is the ONLY output path, and it
is output-only. It must never reference voice-INPUT APIs
(`user_response`, `run_io_loop`, `run_confirmation_loop`,
`start_audio_recording`). `tests/test_one_way.py` enforces this statically.
"""

import logging

logger = logging.getLogger("approval_voice.speak")


def speak(text: str) -> str:
    """Mock of OpenHome speak(): log the utterance and return it.

    Returning the string lets callers (and the demo) capture exactly what would
    have been spoken without touching any real audio device.
    """
    logger.info("[SPEAK] %s", text)
    return text
