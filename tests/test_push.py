"""Unit tests for the PC-side push transport (pc_exporter/push.py, Refs #7).

Covers target parsing (remote / local / Windows-drive disambiguation), the
content-digest idempotency (skip an unchanged re-export, re-push when the remote
is gone or its content changed), the atomic local delivery, and the exponential
backoff (with an injected sleep so tests never wait on real time).

paramiko is NOT exercised here: the SSH transport is covered structurally by the
LocalTransport (same Transport surface) plus a FlakyTransport for backoff; a real
SFTP round-trip needs a live sshd and is the user's on-device step.
"""
import argparse

import pytest

import pc_exporter.__main__ as cli
from pc_exporter.push import (
    LocalTransport,
    PushState,
    Target,
    parse_target,
    push_once,
    push_with_backoff,
)


# --- parse_target ---------------------------------------------------------
def test_parse_target_remote_with_user():
    t = parse_target("user@devkit:/data/approvalvoice/announce_queue.json")
    assert not t.is_local
    assert (t.user, t.host, t.path, t.port) == (
        "user", "devkit", "/data/approvalvoice/announce_queue.json", 22)


def test_parse_target_remote_without_user():
    t = parse_target("devkit:/data/q.json", port=2222)
    assert not t.is_local
    assert t.user is None and t.host == "devkit" and t.port == 2222


def test_parse_target_local_unix_and_windows_and_bare():
    for spec in ("/tmp/drop/q.json", "C:/drop/q.json", r"C:\drop\q.json", "q.json"):
        t = parse_target(spec)
        assert t.is_local, spec
        assert t.host is None and t.path == spec


def test_parse_target_rejects_empty_and_pathless():
    with pytest.raises(ValueError):
        parse_target("")
    with pytest.raises(ValueError):
        parse_target("host:")          # host but no remote path
    with pytest.raises(ValueError):
        parse_target("@host:/p")       # empty host


# --- LocalTransport + idempotency ----------------------------------------
def test_push_once_delivers_then_skips_unchanged(tmp_path):
    local = tmp_path / "announce_queue.json"
    local.write_text('[{"id":"evt-1"}]', encoding="utf-8")
    remote = tmp_path / "remote" / "announce_queue.json"
    transport = LocalTransport()
    state = PushState()

    # First push delivers the bytes atomically.
    assert push_once(local, str(remote), transport, state) is True
    assert remote.read_text(encoding="utf-8") == '[{"id":"evt-1"}]'

    # A re-export with identical bytes is NOT re-pushed (digest match + remote
    # present) -- this is the steady state (export rewrites mtime every loop).
    assert push_once(local, str(remote), transport, state) is False


def test_push_once_repushes_on_content_change(tmp_path):
    local = tmp_path / "announce_queue.json"
    local.write_text("[]", encoding="utf-8")
    remote = tmp_path / "announce_queue.json.remote"
    transport = LocalTransport()
    state = PushState()

    assert push_once(local, str(remote), transport, state) is True
    local.write_text('[{"id":"evt-9"}]', encoding="utf-8")  # content changed
    assert push_once(local, str(remote), transport, state) is True
    assert remote.read_text(encoding="utf-8") == '[{"id":"evt-9"}]'


def test_push_once_repushes_when_remote_disappears(tmp_path):
    # DevKit reboot wipes the file while local content is unchanged: a pure
    # digest skip would strand the device; push_once must re-push (remote size
    # no longer matches -> deliver again).
    local = tmp_path / "announce_queue.json"
    local.write_text('[{"id":"evt-1"}]', encoding="utf-8")
    remote = tmp_path / "remote" / "announce_queue.json"
    transport = LocalTransport()
    state = PushState()

    assert push_once(local, str(remote), transport, state) is True
    remote.unlink()  # simulate the remote file vanishing
    assert push_once(local, str(remote), transport, state) is True
    assert remote.exists()


def test_local_transport_remote_size_none_when_absent(tmp_path):
    transport = LocalTransport()
    assert transport.remote_size(str(tmp_path / "nope.json")) is None


# --- backoff --------------------------------------------------------------
class _FlakyTransport:
    """A transport whose put_atomic fails the first ``fail_times`` calls."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0
        self.delivered = False

    def remote_size(self, remote_path):
        return None

    def put_atomic(self, local_path, remote_path):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise OSError("simulated network failure")
        self.delivered = True

    def close(self):
        pass


def test_push_with_backoff_retries_then_succeeds(tmp_path):
    local = tmp_path / "q.json"
    local.write_text("[]", encoding="utf-8")
    transport = _FlakyTransport(fail_times=2)
    delays = []

    pushed = push_with_backoff(
        local, "/remote/q.json", transport, PushState(),
        attempts=5, base_delay=1.0, sleep=delays.append)

    assert pushed is True and transport.delivered
    assert transport.calls == 3            # 2 failures + 1 success
    assert delays == [1.0, 2.0]            # exponential backoff between the retries


def test_push_with_backoff_raises_after_exhausting_attempts(tmp_path):
    local = tmp_path / "q.json"
    local.write_text("[]", encoding="utf-8")
    transport = _FlakyTransport(fail_times=99)
    delays = []

    with pytest.raises(OSError):
        push_with_backoff(
            local, "/remote/q.json", transport, PushState(),
            attempts=3, base_delay=1.0, max_delay=4.0, sleep=delays.append)
    assert transport.calls == 3            # exactly `attempts` tries
    assert delays == [1.0, 2.0]            # slept between tries, not after the last


# --- loop reconnect on dropped connection (regression: codex P2) ----------
class _DeadAfterConnectTransport:
    """Connects fine, but every push raises -- models a dropped SFTP channel."""

    def remote_size(self, remote_path):
        return None

    def put_atomic(self, local_path, remote_path):
        raise OSError("connection lost (simulated DevKit reboot)")

    def close(self):
        pass


def test_push_loop_reconnects_after_a_dropped_round(tmp_path, monkeypatch):
    # A long-running `push` loop must NOT wedge on a dead transport: paramiko
    # won't reopen a closed channel, so a failed round has to drop + rebuild it.
    # We count make_transport() calls: the up-front connect plus at least one
    # reconnect after the first round's push fails.
    out = tmp_path / "announce_queue.json"

    connects = {"n": 0}

    def _fake_make_transport(target, **kw):
        connects["n"] += 1
        return _DeadAfterConnectTransport()

    def _fake_export(db_path, out_path, since=None, limit=None):
        out_path = tmp_path / "announce_queue.json"
        out_path.write_text("[]", encoding="utf-8")
        return 0

    class _StopLoop(Exception):
        pass

    sleeps = {"n": 0}

    def _fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:            # let two rounds run, then break out
            raise _StopLoop

    monkeypatch.setattr(cli, "make_transport", _fake_make_transport)
    monkeypatch.setattr(cli, "export_queue", _fake_export)
    monkeypatch.setattr(cli.time, "sleep", _fake_sleep)

    args = argparse.Namespace(
        db_path="dummy.db", out=str(out), since=None, limit=None,
        target="user@devkit:/data/announce_queue.json", port=22, identity=None,
        interval=0.0, attempts=1, once=False,
    )

    with pytest.raises(_StopLoop):
        cli._cmd_push(args)

    # 1 up-front connect + >=1 reconnect after the first failed round.
    assert connects["n"] >= 2
