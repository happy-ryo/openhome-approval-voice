#!/usr/bin/env python3
"""CLI for the PC-side approval exporter (Refs #7).

Subcommands:
  export   read state.db read-only, write the section 1.3 queue JSON once.
  serve    periodically re-export (background thread) and serve the file over
           a minimal one-way HTTP server on the LAN.
  push     periodically re-export and PUSH the queue file onto the DevKit over
           scp/sftp (the transport used when the on-device ability cannot make
           outbound HTTP -- urllib is sandbox-denylisted; design.md M3.3.1).

Run as: python -m pc_exporter export --db-path <claude-org>/.state/state.db
        python -m pc_exporter serve   --db-path <claude-org>/.state/state.db
        python -m pc_exporter push    --db-path <claude-org>/.state/state.db \
            --target user@devkit:/data/approvalvoice/announce_queue.json

All help / print strings are ASCII only (cp932 console safety). Queue values
(possibly Japanese) are written to a utf-8 file and served as raw bytes; they
are never printed to the console.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from .core import default_out_path, export_queue, resolve_db_path
from .push import PushState, make_transport, parse_target, push_with_backoff
from .server import DEFAULT_HOST, env_port, make_server


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--db-path",
        default=None,
        help="Path to claude-org state.db (overrides $STATE_DB_PATH and discovery).",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Output queue JSON path (default <repo_root>/.state/announce_queue.json).",
    )
    p.add_argument(
        "--since",
        default=None,
        help="Only export events with occurred_at >= this ISO8601 string "
             "(opt-in; default: full history). Bounds first-run replay.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Export at most the N most-recent items (opt-in; default/0: "
             "unlimited). Count cap only, not a pending-state filter.",
    )


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    db_path = resolve_db_path(args.db_path)
    out_path = Path(args.out).expanduser() if args.out else default_out_path()
    return db_path, out_path


def _cmd_export(args: argparse.Namespace) -> int:
    db_path, out_path = _resolve_paths(args)
    count = export_queue(db_path, out_path, since=args.since, limit=args.limit)
    # ASCII-only: do not print queue values (they may be Japanese).
    print("exported %d item(s) to %s" % (count, out_path))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    db_path, out_path = _resolve_paths(args)
    host = args.host
    port = args.port if args.port is not None else env_port()
    interval = args.interval
    since, limit = args.since, args.limit

    # Export once up front so the file exists before the socket binds. A failure
    # HERE is a startup misconfiguration (bad --db-path, unreadable DB): fail
    # fast rather than binding and serving stale previous-run data (or a silent
    # []). The re-export loop below stays resilient to *transient* mid-run
    # failures (a brief lock / DB swap) once a good initial export has happened.
    try:
        export_queue(db_path, out_path, since=since, limit=limit)
    except Exception as exc:  # noqa: BLE001
        print("initial export failed (not serving): %s" % exc, file=sys.stderr)
        return 2

    stop = threading.Event()

    def _reexport_loop() -> None:
        while not stop.wait(interval):
            try:
                export_queue(db_path, out_path, since=since, limit=limit)
            except Exception as exc:  # noqa: BLE001
                print("re-export failed: %s" % exc, file=sys.stderr)

    worker = threading.Thread(target=_reexport_loop, name="reexport", daemon=True)
    worker.start()

    server = make_server(out_path, host, port)
    bound_host, bound_port = server.server_address[0], server.server_address[1]
    print("serving %s on http://%s:%d/announce_queue.json (re-export %.1fs)"
          % (out_path, bound_host, bound_port, interval))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("shutting down")
    finally:
        stop.set()
        server.server_close()
    return 0


def _cmd_push(args: argparse.Namespace) -> int:
    db_path, out_path = _resolve_paths(args)
    since, limit = args.since, args.limit
    try:
        target = parse_target(args.target, port=args.port)
    except ValueError as exc:
        print("bad --target: %s" % exc, file=sys.stderr)
        return 2

    # Export once up front so a startup misconfig (bad --db-path) fails fast
    # before we open an SSH connection -- same posture as serve.
    try:
        export_queue(db_path, out_path, since=since, limit=limit)
    except Exception as exc:  # noqa: BLE001
        print("initial export failed (not pushing): %s" % exc, file=sys.stderr)
        return 2

    ssh_kwargs = {}
    if not target.is_local:
        if args.identity:
            ssh_kwargs["key_filename"] = args.identity

    # Connect once up front so a real misconfig (bad host/key/missing paramiko)
    # fails fast with a clear exit code, rather than being retried forever as if
    # it were a transient blip.
    try:
        transport = make_transport(target, **ssh_kwargs)
    except Exception as exc:  # noqa: BLE001 - connect / missing paramiko
        print("transport setup failed: %s" % exc, file=sys.stderr)
        return 2

    where = target.path if target.is_local else "%s:%s" % (target.host, target.path)
    print("pushing %s -> %s (re-export %.1fs, target=%s)"
          % (out_path, where, args.interval, "local" if target.is_local else "ssh"))

    state = PushState()

    def _log(msg: str) -> None:
        # ASCII only (queue values are never logged here).
        print(msg)

    # Holder so a failed round can drop a dead connection and the next round can
    # rebuild it. paramiko does NOT auto-reopen a closed SFTP channel, so a DevKit
    # reboot / Wi-Fi drop would otherwise wedge the loop on a dead transport
    # forever. `None` means "reconnect before the next push".
    holder = {"transport": transport}

    def _ensure_transport():
        if holder["transport"] is None:
            holder["transport"] = make_transport(target, **ssh_kwargs)
        return holder["transport"]

    def _drop_transport() -> None:
        t = holder["transport"]
        holder["transport"] = None
        if t is not None:
            try:
                t.close()
            except Exception:  # noqa: BLE001 - already-dead channel close is moot
                pass

    def _one_round() -> None:
        export_queue(db_path, out_path, since=since, limit=limit)
        push_with_backoff(out_path, target.path, _ensure_transport(), state,
                          attempts=args.attempts, log=_log)

    try:
        if args.once:
            _one_round()
            return 0
        while True:
            try:
                _one_round()
            except Exception as exc:  # noqa: BLE001 - keep looping on a bad round
                # The connection may be dead (reboot/Wi-Fi); drop it so the next
                # round reconnects instead of reusing a closed channel forever.
                _drop_transport()
                print("push round failed (will reconnect next interval): %s" % exc,
                      file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("shutting down")
        return 0
    finally:
        _drop_transport()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pc_exporter",
        description="PC-side approval queue exporter: state.db -> section 1.3 JSON -> LAN HTTP.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="Write the queue JSON once and exit.")
    _add_common(p_export)
    p_export.set_defaults(func=_cmd_export)

    p_serve = sub.add_parser("serve", help="Periodically re-export and serve over HTTP.")
    _add_common(p_serve)
    p_serve.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Bind host (default 0.0.0.0 for LAN delivery).",
    )
    p_serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default $APPROVAL_VOICE_HTTP_PORT or 8731).",
    )
    p_serve.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between re-exports (default 2.0).",
    )
    p_serve.set_defaults(func=_cmd_serve)

    p_push = sub.add_parser(
        "push", help="Periodically re-export and push the queue file to the DevKit.")
    _add_common(p_push)
    p_push.add_argument(
        "--target",
        required=True,
        help="Destination: user@host:/remote/path (scp/sftp) or a local path "
             "(no host: -> local copy, e.g. a mounted share or a test dir).",
    )
    p_push.add_argument(
        "--port",
        type=int,
        default=22,
        help="SSH port for a remote target (default 22).",
    )
    p_push.add_argument(
        "--identity",
        default=None,
        help="SSH private key file (default: SSH agent + ~/.ssh default keys).",
    )
    p_push.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between re-export+push rounds (default 2.0).",
    )
    p_push.add_argument(
        "--attempts",
        type=int,
        default=5,
        help="Max transfer attempts per round with exponential backoff (default 5).",
    )
    p_push.add_argument(
        "--once",
        action="store_true",
        help="Export and push a single time, then exit (no loop).",
    )
    p_push.set_defaults(func=_cmd_push)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
