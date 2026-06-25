#!/usr/bin/env python3
"""CLI for the PC-side approval exporter (Refs #7).

Subcommands:
  export   read state.db read-only, write the section 1.3 queue JSON once.
  serve    periodically re-export (background thread) and serve the file over
           a minimal one-way HTTP server on the LAN.

Run as: python -m pc_exporter export --db-path <claude-org>/.state/state.db
        python -m pc_exporter serve   --db-path <claude-org>/.state/state.db

All help / print strings are ASCII only (cp932 console safety). Queue values
(possibly Japanese) are written to a utf-8 file and served as raw bytes; they
are never printed to the console.
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from .core import default_out_path, export_queue, resolve_db_path
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


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    db_path = resolve_db_path(args.db_path)
    out_path = Path(args.out).expanduser() if args.out else default_out_path()
    return db_path, out_path


def _cmd_export(args: argparse.Namespace) -> int:
    db_path, out_path = _resolve_paths(args)
    count = export_queue(db_path, out_path)
    # ASCII-only: do not print queue values (they may be Japanese).
    print("exported %d item(s) to %s" % (count, out_path))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    db_path, out_path = _resolve_paths(args)
    host = args.host
    port = args.port if args.port is not None else env_port()
    interval = args.interval

    # Export once up front so the file exists before the socket binds.
    try:
        export_queue(db_path, out_path)
    except Exception as exc:  # noqa: BLE001
        print("initial export failed: %s" % exc, file=sys.stderr)

    stop = threading.Event()

    def _reexport_loop() -> None:
        while not stop.wait(interval):
            try:
                export_queue(db_path, out_path)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
