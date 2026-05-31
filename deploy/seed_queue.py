#!/usr/bin/env python3
"""スモーク用サンプルキューを DevKit のキューパスへ配置する小ヘルパ。

approval-voice の ability は **単一の JSON ファイル**（中身は §1.3 アイテムの配列）を
`APPROVAL_VOICE_QUEUE` で受け取り、未読分を逐語読み上げする。本ヘルパは
canonical な 4 ゲートサンプル `examples/announce_queue.json`（判断/承認系の
worker_complete / ci_merge / escalation / reply_relay を 1 件ずつ）を、
指定のキューファイルパスへ **コピー** する（committed 原本は汚さない。単一の真実源）。

> 重要（詰まりやすい open(QUEUE_PATH)）:
> ability(background.py) のキュー解決は **`APPROVAL_VOICE_QUEUE`（ファイルパス）**。
> 既定は `~/.openhome/approval_voice/announce_queue.json`。
> seed と ability で **同じ絶対ファイルパス**を指すこと。ズレると「キューに入れた
> のに ability が見つけられない」になる。既読カーソルは別ファイル
> `APPROVAL_VOICE_SEEN`（既定 `~/.openhome/approval_voice/announce_seen.json`）。

キューパスの解決順:
1. `--queue` 引数（ファイルパス）
2. 環境変数 `APPROVAL_VOICE_QUEUE`（ability と同一）
3. 既定 `~/.openhome/approval_voice/announce_queue.json`（ability の既定と一致）

使い方::

    # 環境変数でキューパスを指定（推奨。ability も同じ env を読む）
    export APPROVAL_VOICE_QUEUE=/data/approval_voice/announce_queue.json
    py -3 deploy/seed_queue.py

    # もしくは明示
    py -3 deploy/seed_queue.py --queue /data/approval_voice/announce_queue.json

    # 1 件だけ試したいとき
    py -3 deploy/seed_queue.py --first-only
"""
from __future__ import annotations

import argparse
import json
import os
import shutil

_DEPLOY_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_DEPLOY_DIR)
_SAMPLE = os.path.join(_REPO_ROOT, "examples", "announce_queue.json")
_ENV = "APPROVAL_VOICE_QUEUE"
_DEFAULT = os.path.join(
    os.path.expanduser("~"), ".openhome", "approval_voice", "announce_queue.json"
)


def resolve_queue_path(arg: str | None) -> str:
    if arg:
        return arg
    return os.environ.get(_ENV) or _DEFAULT


def seed(queue_path: str, first_only: bool = False) -> int:
    """サンプルをキューファイルへ配置する。配置した件数を返す。"""
    os.makedirs(os.path.dirname(os.path.abspath(queue_path)), exist_ok=True)
    if first_only:
        with open(_SAMPLE, encoding="utf-8") as f:
            items = json.load(f)
        items = items[:1]
        with open(queue_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        return len(items)
    shutil.copy2(_SAMPLE, queue_path)
    with open(queue_path, encoding="utf-8") as f:
        return len(json.load(f))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--queue", default=None, help="配置先キューファイルパス")
    p.add_argument(
        "--first-only", action="store_true", help="先頭 1 件だけ配置（最小スモーク）"
    )
    args = p.parse_args(argv)
    qpath = resolve_queue_path(args.queue)
    n = seed(qpath, first_only=args.first_only)
    print(f"seeded {n} gate(s) into queue file: {qpath}")
    print("  (ability は APPROVAL_VOICE_QUEUE で同じパスを読むこと)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
