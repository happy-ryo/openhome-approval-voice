"""On-device daemon path test (openhome_ability/background.py).

Drives `ApprovalVoiceWatcher` with a fake async `capability_worker` + in-memory
name-based storage — no OpenHome runtime — to prove the smoke path that the
requester relies on (they can't read device logs, so audible speak() is the only
ground truth): an *enabled* daemon, given an empty store, bootstraps the sample
and reads all four gates aloud exactly once, interrupting once before speaking,
then dedups on the next poll.
"""

import asyncio
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def _install_src_stub():
    """Minimal `src.*` so openhome_ability.background imports without the runtime."""
    if "src" in sys.modules:
        return
    src = types.ModuleType("src")
    agent = types.ModuleType("src.agent")
    cap = types.ModuleType("src.agent.capability")
    cw = types.ModuleType("src.agent.capability_worker")
    mn = types.ModuleType("src.main")

    class MatchingCapability:  # noqa: D401
        pass

    class CapabilityWorker:
        def __init__(self, *a, **k):
            pass

    class AgentWorker:
        pass

    cap.MatchingCapability = MatchingCapability
    cw.CapabilityWorker = CapabilityWorker
    mn.AgentWorker = AgentWorker
    src.agent = agent
    agent.capability = cap
    agent.capability_worker = cw
    sys.modules.update(
        {
            "src": src,
            "src.agent": agent,
            "src.agent.capability": cap,
            "src.agent.capability_worker": cw,
            "src.main": mn,
        }
    )


_install_src_stub()
sys.path.insert(0, str(_ROOT / "openhome_ability"))
import background  # noqa: E402


class _FakeLog:
    def info(self, *a, **k):
        pass


class _FakeWorker:
    editor_logging_handler = _FakeLog()


class _FakeCap:
    """In-memory stand-in for capability_worker (name-based storage + speak log)."""

    def __init__(self):
        self.store = {}
        self.spoken = []
        self.interrupts = 0

    async def check_if_file_exists(self, name, temp):
        return name in self.store

    async def read_file(self, name, temp):
        return self.store[name]

    async def write_file(self, name, content, temp):
        self.store[name] = content

    async def delete_file(self, name, temp):
        self.store.pop(name, None)

    async def speak(self, text):
        self.spoken.append(text)

    async def send_interrupt_signal(self):
        self.interrupts += 1


def _make_watcher():
    w = background.ApprovalVoiceWatcher()
    w.worker = _FakeWorker()
    w.capability_worker = _FakeCap()
    return w


def test_enabled_daemon_bootstraps_and_speaks_four_then_dedups():
    w = _make_watcher()

    async def run():
        await w._maybe_bootstrap()       # empty store -> seed sample
        first = await w._poll_tick()     # reads + speaks the 4 gates
        second = await w._poll_tick()    # read-cursor suppresses re-readout
        return first, second

    first, second = asyncio.run(run())
    assert len(first) == 4, first
    assert second == []
    assert w.capability_worker.interrupts == 1   # interrupt once, before speaking
    assert len(w.capability_worker.spoken) == 4  # never double-spoke


def test_bootstrap_noop_when_queue_present():
    w = _make_watcher()
    w.capability_worker.store[background.QUEUE_NAME] = "[]"  # already seeded

    async def run():
        await w._maybe_bootstrap()
        return await w._poll_tick()

    spoken = asyncio.run(run())
    assert spoken == []                              # empty queue -> nothing said
    assert w.capability_worker.store[background.QUEUE_NAME] == "[]"  # not overwritten
