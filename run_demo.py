"""End-to-end M2 demo: mock queue -> dedup -> render -> speak() to stdout.

Run:  py -3 run_demo.py

Exercises the full one-way data path with OpenHome mocked. Nothing here touches
real audio or any voice-INPUT API. A second poll tick is run to show the
read-cursor suppresses already-spoken items (no double readout).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from approval_voice.ability import ApprovalVoiceAbility
from approval_voice.bridge import load_queue

QUEUE_PATH = Path(__file__).parent / "examples" / "announce_queue.json"


def main() -> None:
    # Windows consoles default to cp932; Japanese readouts need utf-8 (or print
    # raises UnicodeEncodeError even though the strings themselves are fine).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    items = load_queue(QUEUE_PATH)
    ability = ApprovalVoiceAbility()

    print(f"=== poll tick 1: {len(items)} item(s) in queue ===")
    spoken = ability.poll_once(items)
    print(f"--- spoke {len(spoken)} item(s) ---\n")

    print("=== poll tick 2: same queue, nothing new ===")
    spoken_again = ability.poll_once(items)
    print(f"--- spoke {len(spoken_again)} item(s) (read-cursor dedup) ---")


if __name__ == "__main__":
    main()
