"""Live PULL primary: requests.get(PC exporter) -> QUEUE_STORE -> readout (Refs #7).

Sibling of test_push_loopback.py (push fallback). Pins the production PRIMARY
transport added on feat/openhome-pull-primary: each tick the daemon GETs the PC
exporter's §1.3 queue and writes it into its own storage, then the unchanged
read/dedup/render/speak pass runs. Covered here against a MOCK `requests` (no real
socket): 200 happy path, non-200, timeout/exception, and malformed body — and the
fallback contract that a failed pull never clobbers a good prior queue (so a
PC-side push, or a prior pull, survives). The one-way property (GET only, never a
write-back) is pinned structurally by tests/test_outbound_one_way.py; here we also
assert behaviourally that only `.get` is ever called.
"""
import asyncio
import importlib
import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent


def _install_src_stubs():
    """Minimal `src.*` runtime stubs so `openhome_ability.background` imports
    (mirrors deploy/build_zip.py + test_push_loopback.py)."""
    if "src.agent.capability" in sys.modules:
        return
    src = types.ModuleType("src")
    agent = types.ModuleType("src.agent")
    capability = types.ModuleType("src.agent.capability")
    capability_worker = types.ModuleType("src.agent.capability_worker")
    main = types.ModuleType("src.main")

    class MatchingCapability:
        pass

    class CapabilityWorker:
        def __init__(self, *a, **k):
            pass

    class AgentWorker:
        pass

    capability.MatchingCapability = MatchingCapability
    capability_worker.CapabilityWorker = CapabilityWorker
    main.AgentWorker = AgentWorker
    src.agent = agent
    agent.capability = capability
    agent.capability_worker = capability_worker
    sys.modules.update({
        "src": src,
        "src.agent": agent,
        "src.agent.capability": capability,
        "src.agent.capability_worker": capability_worker,
        "src.main": main,
    })


def _install_bundle_aliases():
    """Resolve background.py's relative `from .approval_voice...` to the real
    bundled package (mirrors test_push_loopback.py)."""
    import approval_voice

    sys.modules.setdefault("openhome_ability.approval_voice", approval_voice)
    for sub in ("bridge", "poller", "renderer", "sample", "storage"):
        sys.modules.setdefault(
            f"openhome_ability.approval_voice.{sub}",
            importlib.import_module(f"approval_voice.{sub}"),
        )


_install_src_stubs()
_install_bundle_aliases()

from openhome_ability import background  # noqa: E402
from approval_voice.renderer import ONE_WAY_SUFFIX  # noqa: E402
from approval_voice.sample import SAMPLE_NOTIFICATIONS  # noqa: E402
from approval_voice.bridge import notifications_to_payload  # noqa: E402
from approval_voice.schema import GATES  # noqa: E402
from approval_voice.storage import QUEUE_STORE  # noqa: E402

import json  # noqa: E402


# --- fakes (mirror test_push_loopback.py) ---------------------------------
class _FakeCapabilityWorker:
    def __init__(self):
        self.storage = {}
        self.spoken = []
        self.interrupts = 0
        self.writes = 0  # count write_file calls (to assert idempotent skip)

    async def check_if_file_exists(self, name, temp):
        return name in self.storage

    async def read_file(self, name, temp):
        return self.storage[name]

    async def write_file(self, name, text, temp):
        self.storage[name] = text
        self.writes += 1

    async def delete_file(self, name, temp):
        self.storage.pop(name, None)

    async def send_interrupt_signal(self):
        self.interrupts += 1

    async def speak(self, text):
        self.spoken.append(text)


class _FakeLog:
    def info(self, *a, **k):
        pass


class _StopLoop(Exception):
    pass


class _FakeSessionTasks:
    def __init__(self, ticks=1):
        self._remaining = ticks

    async def sleep(self, _seconds):
        self._remaining -= 1
        if self._remaining <= 0:
            raise _StopLoop

    def create(self, coro):
        coro.close()


class _FakeWorker:
    def __init__(self, ticks=1):
        self.editor_logging_handler = _FakeLog()
        self.session_tasks = _FakeSessionTasks(ticks)


def _make_watcher(ticks=1):
    w = background.ApprovalVoiceWatcher()
    w.worker = _FakeWorker(ticks)
    w.capability_worker = _FakeCapabilityWorker()
    return w


def _run_watch_queue(watcher):
    async def scenario():
        try:
            await watcher.watch_queue()
        except _StopLoop:
            pass

    asyncio.run(scenario())


# --- fake requests --------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class _FakeRequests:
    """Records calls and replays a scripted response or raises an exception."""

    def __init__(self, *, status_code=200, text="", raise_exc=None):
        self.status_code = status_code
        self.text = text
        self.raise_exc = raise_exc
        self.calls = []  # list of (verb, url, kwargs)

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeResponse(self.status_code, self.text)

    # Any write verb being called would be a one-way violation; record it so a
    # regression surfaces as a test failure rather than a silent send.
    def post(self, *a, **k):  # pragma: no cover - must never be called
        self.calls.append(("post", a, k))
        raise AssertionError("daemon must never POST (one-way invariant)")


def _install_fake_requests(monkeypatch, fake):
    """Make the method-local `import requests` in background.py resolve to `fake`."""
    monkeypatch.setitem(sys.modules, "requests", fake)


def _sample_body() -> str:
    """The canonical 4-gate §1.3 JSON the PC exporter would serve."""
    return json.dumps(notifications_to_payload(SAMPLE_NOTIFICATIONS),
                      ensure_ascii=False, indent=2)


# --- tests ----------------------------------------------------------------
def test_pull_writes_fetched_body_and_reads_aloud(monkeypatch):
    body = _sample_body()
    fake = _FakeRequests(status_code=200, text=body)
    _install_fake_requests(monkeypatch, fake)

    watcher = _make_watcher()

    async def scenario():
        await watcher._pull_into_storage(1)        # GET -> write QUEUE_STORE
        await watcher._drain_queue_once(1)         # read -> speak

    asyncio.run(scenario())
    cw = watcher.capability_worker

    # The fetched body was persisted verbatim and rendered (4 gates, verbatim).
    assert cw.storage[QUEUE_STORE] == body
    assert len(cw.spoken) == 4
    assert all(line.endswith(ONE_WAY_SUFFIX) for line in cw.spoken)
    assert cw.interrupts == 1
    # GET-only: exactly one call, and it was a GET (one-way, behavioural).
    assert [c[0] for c in fake.calls] == ["get"]
    assert cw.storage  # sanity


def test_pull_failure_keeps_existing_storage(monkeypatch):
    # A push (or prior pull) already left a good queue; a failed pull must NOT
    # wipe it -- the daemon falls back to the existing storage and still speaks.
    fake = _FakeRequests(raise_exc=TimeoutError("connect timed out"))
    _install_fake_requests(monkeypatch, fake)

    body = _sample_body()
    watcher = _make_watcher()
    watcher.capability_worker.storage[QUEUE_STORE] = body  # pretend push delivered

    async def scenario():
        await watcher._pull_into_storage(1)        # GET raises -> storage untouched
        await watcher._drain_queue_once(1)

    asyncio.run(scenario())
    cw = watcher.capability_worker
    assert cw.storage[QUEUE_STORE] == body         # prior queue preserved
    assert len(cw.spoken) == 4                      # still read aloud from fallback


def test_pull_idempotent_skips_rewrite_when_unchanged(monkeypatch):
    # The steady state is an unchanged queue re-served every tick. The second
    # identical pull must NOT rewrite storage (write amplification guard); the
    # seen-cursor, not a rewrite, prevents double-speak.
    body = _sample_body()
    fake = _FakeRequests(status_code=200, text=body)
    _install_fake_requests(monkeypatch, fake)

    watcher = _make_watcher()

    async def scenario():
        await watcher._pull_into_storage(1)   # first: writes
        await watcher._pull_into_storage(2)   # second: identical -> skip write

    asyncio.run(scenario())
    cw = watcher.capability_worker
    assert cw.storage[QUEUE_STORE] == body
    assert cw.writes == 1                      # only the first pull wrote
    assert [c[0] for c in fake.calls] == ["get", "get"]  # both ticks GET, no POST


def test_pull_non_200_does_not_touch_storage(monkeypatch):
    fake = _FakeRequests(status_code=404, text="not found")
    _install_fake_requests(monkeypatch, fake)

    body = _sample_body()
    watcher = _make_watcher()
    watcher.capability_worker.storage[QUEUE_STORE] = body

    asyncio.run(watcher._pull_into_storage(1))
    assert watcher.capability_worker.storage[QUEUE_STORE] == body  # unchanged


def test_pull_bad_body_not_persisted(monkeypatch):
    # A 200 with a non-§1.3 / unparseable body must not overwrite a good queue.
    fake = _FakeRequests(status_code=200, text="{ this is not valid json")
    _install_fake_requests(monkeypatch, fake)

    body = _sample_body()
    watcher = _make_watcher()
    watcher.capability_worker.storage[QUEUE_STORE] = body

    asyncio.run(watcher._pull_into_storage(1))
    assert watcher.capability_worker.storage[QUEUE_STORE] == body  # good prior kept


def test_pull_into_empty_storage_when_endpoint_down(monkeypatch):
    # No prior storage AND pull fails: nothing to read, no crash, no speak.
    fake = _FakeRequests(raise_exc=ConnectionError("no route to host"))
    _install_fake_requests(monkeypatch, fake)

    watcher = _make_watcher()

    async def scenario():
        await watcher._pull_into_storage(1)
        await watcher._drain_queue_once(1)

    asyncio.run(scenario())
    cw = watcher.capability_worker
    assert QUEUE_STORE not in cw.storage
    assert cw.spoken == []


def test_watch_queue_pull_primary_end_to_end(monkeypatch):
    # Drive the REAL daemon entry (watch_queue) with PULL_ENABLED True and
    # SMOKE_AUTOSEED False: the loop must PULL the live queue and read it aloud,
    # with no self-seed. This is the live-integration path (minus the real socket).
    monkeypatch.setattr(background, "PULL_ENABLED", True)
    monkeypatch.setattr(background, "SMOKE_AUTOSEED", False)
    body = _sample_body()
    fake = _FakeRequests(status_code=200, text=body)
    _install_fake_requests(monkeypatch, fake)

    watcher = _make_watcher(ticks=1)
    assert QUEUE_STORE not in watcher.capability_worker.storage  # empty at start
    _run_watch_queue(watcher)

    cw = watcher.capability_worker
    assert cw.storage[QUEUE_STORE] == body         # pulled into storage
    assert {i["gate"] for i in json.loads(cw.storage[QUEUE_STORE])} == set(GATES)
    assert len(cw.spoken) == 4                      # pulled gates read aloud once
    assert cw.interrupts == 1
    assert [c[0] for c in fake.calls] == ["get"]    # GET only (one-way)


def test_watch_queue_pull_disabled_is_storage_only(monkeypatch):
    # With PULL_ENABLED False the daemon must NOT call requests at all and instead
    # read whatever a push left in storage (the fallback / regression guard).
    monkeypatch.setattr(background, "PULL_ENABLED", False)
    monkeypatch.setattr(background, "SMOKE_AUTOSEED", False)
    fake = _FakeRequests(status_code=200, text=_sample_body())
    _install_fake_requests(monkeypatch, fake)

    body = _sample_body()
    watcher = _make_watcher(ticks=1)
    watcher.capability_worker.storage[QUEUE_STORE] = body  # push delivered

    _run_watch_queue(watcher)
    cw = watcher.capability_worker
    assert fake.calls == []                          # no network when pull disabled
    assert len(cw.spoken) == 4                       # read from pushed storage
