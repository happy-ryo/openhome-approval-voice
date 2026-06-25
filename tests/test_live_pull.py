"""Loopback proof of the live PC->DevKit pull (design.md §M3.3.1 / §M3.1-s.5).

A local stub HTTP server returns the §1.3 4-gate array (the exact envelope PR1's
exporter must emit). We drive the REAL on-device daemon
(`openhome_ability.background.ApprovalVoiceWatcher`) with a fake `capability_worker`
and assert the full path:

    GET source -> write_file(QUEUE_STORE) -> read -> dedup -> render -> speak

reads all 4 gates verbatim once, interrupts exactly once, dedups on the next pass,
and that the server only ever saw GET requests (structural one-way). The real
on-device E2E (audible readout on hardware) is the user's step; this pins the code
path locally. Tests are not bundled, so stdlib http/json/threading are fine here.
"""

import asyncio
import importlib
import json
import sys
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

_REPO_ROOT = Path(__file__).parent.parent


def _install_src_stubs():
    """Minimal `src.*` runtime stubs so `openhome_ability.background` imports.

    Mirrors deploy/build_zip.py's verify stub: the framework classes the ability
    subclasses/constructs do not live in this repo. `approval_voice` is the real
    bundled package (resolved by relative import), NOT stubbed.
    """
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
    """Resolve `openhome_ability.background`'s relative `from .approval_voice...`.

    In the deployed zip, `approval_voice/` is nested inside the wrap folder beside
    `background.py`, so the relative import resolves. In the source tree the pure
    package lives at repo root, so we register the real submodules under the
    `openhome_ability.approval_voice.*` names the relative import expects (same
    objects — no duplicate logic).
    """
    import approval_voice

    sys.modules.setdefault("openhome_ability.approval_voice", approval_voice)
    for sub in ("bridge", "poller", "renderer", "sample", "source", "storage"):
        sys.modules.setdefault(
            f"openhome_ability.approval_voice.{sub}",
            importlib.import_module(f"approval_voice.{sub}"),
        )


_install_src_stubs()
_install_bundle_aliases()

from openhome_ability import background  # noqa: E402
from approval_voice.renderer import ONE_WAY_SUFFIX  # noqa: E402
from approval_voice.schema import GATES  # noqa: E402
from approval_voice.storage import QUEUE_STORE  # noqa: E402


class _FakeCapabilityWorker:
    """In-memory stand-in for the OpenHome storage + speech API (all async)."""

    def __init__(self):
        self.storage = {}
        self.spoken = []
        self.interrupts = 0

    async def check_if_file_exists(self, name, temp):
        return name in self.storage

    async def read_file(self, name, temp):
        return self.storage[name]

    async def write_file(self, name, text, temp):
        self.storage[name] = text

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
    """Raised from the patched sleep to break watch_queue's infinite loop."""


class _FakeSessionTasks:
    """Lets watch_queue run exactly `ticks` iterations, then stops the loop."""

    def __init__(self, ticks=1):
        self._remaining = ticks

    async def sleep(self, _seconds):
        self._remaining -= 1
        if self._remaining <= 0:
            raise _StopLoop

    def create(self, coro):
        coro.close()  # we drive watch_queue directly; don't schedule it twice


class _FakeWorker:
    def __init__(self, ticks=1):
        self.editor_logging_handler = _FakeLog()
        self.session_tasks = _FakeSessionTasks(ticks)


def _make_watcher():
    w = background.ApprovalVoiceWatcher()
    w.worker = _FakeWorker()
    w.capability_worker = _FakeCapabilityWorker()
    return w


class _Stub:
    """Serve a fixed JSON body and record the HTTP methods it was asked for."""

    def __init__(self, body: str):
        self.methods = []
        body_bytes = body.encode("utf-8")
        methods = self.methods

        class Handler(BaseHTTPRequestHandler):
            def _send(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)

            def do_GET(self):
                methods.append("GET")
                self._send()

            def do_POST(self):  # must never be hit — proves no write-back
                methods.append("POST")
                self.send_response(405)
                self.end_headers()

            def do_PUT(self):
                methods.append("PUT")
                self.send_response(405)
                self.end_headers()

            def log_message(self, *a):  # silence the default stderr logging
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _sample_body() -> str:
    queue = _REPO_ROOT / "examples" / "announce_queue.json"
    return queue.read_text(encoding="utf-8")


def test_live_pull_reads_all_four_gates_verbatim_then_dedups():
    watcher = _make_watcher()
    with _Stub(_sample_body()) as stub:
        url = f"http://127.0.0.1:{stub.port}/announce_queue.json"

        async def scenario():
            await watcher._refresh_from_source(url, 1)
            await watcher._drain_queue_once(1)   # first read pass -> speak all
            first = list(watcher.capability_worker.spoken)
            await watcher._refresh_from_source(url, 2)
            await watcher._drain_queue_once(2)   # second pass -> dedup, no new speak
            return first

        first = asyncio.run(scenario())

    cw = watcher.capability_worker
    # The fetched envelope landed in the daemon's own storage.
    assert QUEUE_STORE in cw.storage
    assert {i["gate"] for i in json.loads(cw.storage[QUEUE_STORE])} == set(GATES)
    # All 4 gates spoken once, verbatim (each closes with the one-way reminder).
    assert len(first) == 4
    assert all(line.endswith(ONE_WAY_SUFFIX) for line in first)
    # Interrupt sent exactly once before the batch (not per item, not per tick).
    assert cw.interrupts == 1
    # Second pass added nothing: read cursor suppressed the re-readout.
    assert cw.spoken == first
    # Structural one-way: the server was only ever GET'd, never written back to.
    assert set(stub.methods) == {"GET"}


def test_rejected_scheme_does_not_fetch_or_write():
    watcher = _make_watcher()

    async def scenario():
        # A file:// URL must be refused by the guard — no storage write at all.
        await watcher._refresh_from_source("file:///etc/passwd", 1)

    asyncio.run(scenario())
    assert QUEUE_STORE not in watcher.capability_worker.storage
    assert watcher.capability_worker.spoken == []


def test_garbage_body_does_not_clobber_existing_queue():
    watcher = _make_watcher()
    # Seed a known-good queue first.
    good = _sample_body()
    watcher.capability_worker.storage[QUEUE_STORE] = good

    with _Stub("this is not json") as stub:
        url = f"http://127.0.0.1:{stub.port}/announce_queue.json"

        async def scenario():
            await watcher._refresh_from_source(url, 1)

        asyncio.run(scenario())

    # Unparseable 200 body was discarded; the prior good queue is intact.
    assert watcher.capability_worker.storage[QUEUE_STORE] == good


def _run_watch_queue_once(watcher, source_url):
    """Drive exactly one tick of the REAL watch_queue loop with ANNOUNCE_SOURCE_URL
    patched (it is imported by value into the background module at load time)."""
    original = background.ANNOUNCE_SOURCE_URL
    background.ANNOUNCE_SOURCE_URL = source_url
    try:
        async def scenario():
            try:
                await watcher.watch_queue()
            except _StopLoop:
                pass

        asyncio.run(scenario())
    finally:
        background.ANNOUNCE_SOURCE_URL = original


def test_watch_queue_tick_pulls_then_drains_when_source_set():
    # Exercises the integration point: the loop must pull from the source THEN
    # drain/speak. Deleting the _refresh_from_source call (or breaking the
    # `if ANNOUNCE_SOURCE_URL:` gate) would make this fail.
    watcher = _make_watcher()
    with _Stub(_sample_body()) as stub:
        url = f"http://127.0.0.1:{stub.port}/announce_queue.json"
        _run_watch_queue_once(watcher, url)

    cw = watcher.capability_worker
    assert QUEUE_STORE in cw.storage              # source was pulled into storage
    assert len(cw.spoken) == 4                    # ...and drained/spoken in the same tick
    assert all(line.endswith(ONE_WAY_SUFFIX) for line in cw.spoken)
    assert cw.interrupts == 1
    assert set(stub.methods) == {"GET"}


def test_watch_queue_does_not_pull_when_source_unset():
    # The enable gate: with ANNOUNCE_SOURCE_URL=None the loop must NOT fetch.
    watcher = _make_watcher()
    with _Stub(_sample_body()) as stub:
        _run_watch_queue_once(watcher, None)
        # The server was never contacted, and nothing was spoken (empty storage).
        assert stub.methods == []
    assert watcher.capability_worker.spoken == []
    assert QUEUE_STORE not in watcher.capability_worker.storage


def test_live_pull_drops_unknown_fields_but_speaks_all_gates():
    # §1.3 envelope tolerance: an exporter body carrying internal fields outside
    # the ITEM_FIELDS whitelist must still parse, the internals must never reach
    # the spoken output (public hygiene), and all 4 gates still read aloud.
    items = json.loads(_sample_body())
    for it in items:
        it["internal_state_path"] = "C:/secret/state.json"
        it["hook_name"] = "PreToolUse"
    body = json.dumps(items, ensure_ascii=False)

    watcher = _make_watcher()
    with _Stub(body) as stub:
        url = f"http://127.0.0.1:{stub.port}/announce_queue.json"

        async def scenario():
            await watcher._refresh_from_source(url, 1)
            await watcher._drain_queue_once(1)

        asyncio.run(scenario())

    spoken = watcher.capability_worker.spoken
    assert len(spoken) == 4
    assert all(line.endswith(ONE_WAY_SUFFIX) for line in spoken)
    blob = "\n".join(spoken)
    assert "secret" not in blob and "PreToolUse" not in blob
