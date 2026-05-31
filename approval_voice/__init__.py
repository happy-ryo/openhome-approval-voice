"""openhome-approval-voice — M2 PoC scaffold.

One-way voice readout of Secretary `awaiting_user` approval gates.
Voice OUTPUT only (speak); no voice INPUT capture. See docs/design.md (M1).

OpenHome is fully mocked in M2; real connection is M3.
"""

from .schema import GATES, AnnounceItem
from .renderer import render_speech
from .speak import speak

__all__ = ["GATES", "AnnounceItem", "render_speech", "speak"]
