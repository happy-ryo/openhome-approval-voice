#!/usr/bin/env python3
"""HTTP delivery + envelope tests for pc_exporter.server (Refs #7).

The HTTP envelope here is FIXED and must match the PR2 on-device ability:
  GET /announce_queue.json -> 200, Content-Type: application/json (bare),
      body = a JSON array of section 1.3 AnnounceItem objects.
Any other path -> 404; any non-GET method -> 405 (one-way invariant).
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from exporter_helpers import insert_event, seed_state_db, awaiting

from pc_exporter import core, server


@pytest.fixture
def running_server(tmp_path):
    """Start a ThreadingHTTPServer on an ephemeral loopback port."""
    queue = tmp_path / "announce_queue.json"
    core.atomic_write_json([], queue)  # ensure file exists
    srv = server.make_server(queue, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, queue, port
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def _get(port: int, path: str = server.QUEUE_ROUTE):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, resp.getheader("Content-Type"), body
    finally:
        conn.close()


def _request(port: int, method: str, path: str = server.QUEUE_ROUTE):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def test_serve_returns_valid_section13_array(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #1")},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_to_user", "Issue #7")},
    ])
    queue = tmp_path / "announce_queue.json"
    n = core.export_queue(db, queue)
    assert n == 2

    srv = server.make_server(queue, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, ctype, body = _get(port)
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)

    assert status == 200
    assert ctype == "application/json"  # bare, no charset suffix (PR2 match)
    data = json.loads(body.decode("utf-8"))
    assert isinstance(data, list)
    assert {item["gate"] for item in data} == {"worker_complete", "escalation"}
    for item in data:
        assert set(item.keys()) == set(core.FIELDS)


def test_get_reflects_reexport(tmp_path):
    """The handler reads the file fresh each GET, so a re-export is visible."""
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #1")},
    ])
    queue = tmp_path / "announce_queue.json"
    core.export_queue(db, queue)

    srv = server.make_server(queue, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        _, _, body1 = _get(port)
        assert len(json.loads(body1)) == 1
        # add another awaiting_user row and re-export to the same file
        insert_event(db, "2026-06-26T05:00:00.000Z", "ci_green_merge_gate", "PR #2")
        core.export_queue(db, queue)
        _, _, body2 = _get(port)
        assert len(json.loads(body2)) == 2
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_other_path_404(running_server):
    _, _, port = running_server
    status, _, _ = _get(port, "/something_else")
    assert status == 404


def test_root_path_404(running_server):
    _, _, port = running_server
    status, _, _ = _get(port, "/")
    assert status == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
def test_non_get_methods_405(running_server, method):
    _, _, port = running_server
    assert _request(port, method) == 405


def test_missing_queue_file_serves_empty_array(tmp_path):
    """If the queue file does not exist yet, GET still returns a valid []."""
    queue = tmp_path / "nope.json"
    srv = server.make_server(queue, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, ctype, body = _get(port)
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)
    assert status == 200
    assert ctype == "application/json"
    assert json.loads(body) == []


def test_env_port_default(monkeypatch):
    monkeypatch.delenv("APPROVAL_VOICE_HTTP_PORT", raising=False)
    assert server.env_port() == 8731
    monkeypatch.setenv("APPROVAL_VOICE_HTTP_PORT", "9000")
    assert server.env_port() == 9000
