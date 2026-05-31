#!/usr/bin/env python3
"""Build a deployable OpenHome capability zip for approval-voice.

単一の真実源 (single source of truth):
- このスクリプトはリポジトリ最上位の `openhome_ability/`（実機 ability=
  main.py / background.py / __init__.py / requirements.txt）を zip ルートへ、
  純ロジック `approval_voice/`（schema/renderer/poller/bridge/...）をその直下へ
  **そのまま同梱**する。重複実装は作らない。
- `background.py` は起動時に自分のディレクトリを sys.path へ積み
  （`sys.path.insert(0, os.path.dirname(__file__))`）、`from approval_voice ...`
  する。したがって zip 内では `approval_voice/` が `background.py` と
  **同じ階層**に無いと import が壊れる。これが本タスク最大の注意点
  （過去に import/パス不一致を最重要と指摘）。

zip レイアウト（既定 = ルート直置き）::

    approval-voice-ability.zip
    ├── main.py            ← interactive entry（status 読み上げ）
    ├── background.py      ← always-on watcher（キュー polling→逐語 speak）
    ├── __init__.py
    ├── requirements.txt   ← stdlib only
    └── approval_voice/    ← background.py が import する純ロジック（同梱必須）
        ├── __init__.py
        ├── schema.py
        ├── renderer.py
        ├── poller.py
        ├── bridge.py
        ├── ability.py
        └── speak.py

`--root-folder NAME` を付けると全ファイルを `NAME/` 配下に入れる
（OpenHome がラップフォルダ付き zip を要求した場合の保険）。

検証 (verify) — ローカルで import 健全性を事前実証する:
1. py_compile で全 .py を構文チェック。
2. 生成 zip を新しい temp へ展開し、**別プロセス**で実機 ability を import する。
   `background.py` / `main.py` は OpenHome ランタイム `src.*` を import するが、
   それは本リポに無い。そこで temp に **最小スタブ `src` パッケージ**を置き、
   cwd=展開先（=background.py が sys.path に積むのと同じ場所）で
   `import background`・`import main` を実行する。これにより
   「展開後のレイアウトで background.py の `from approval_voice...` が解決する」
   ＝ import/パス不一致が無いことを実証する（スタブは src.* だけを満たし、
   approval_voice は **同梱した本物**を解決させる）。
3. 続けて同プロセスで `examples/announce_queue.json`（§1.3 準拠の 4 ゲート
   サンプル）を実データ経路 load_queue→ReadCursor→render_speech に流し、
   4 件レンダリングできることを確認する。

使い方::

    py -3 deploy/build_zip.py                 # dist/approval-voice-ability.zip を生成+検証
    py -3 deploy/build_zip.py --root-folder approval-voice
    py -3 deploy/build_zip.py --no-verify     # 検証を省略（非推奨）
"""
from __future__ import annotations

import argparse
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile

# --- paths ----------------------------------------------------------------
_DEPLOY_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_DEPLOY_DIR)
_SRC_ABILITY = os.path.join(_REPO_ROOT, "openhome_ability")
_SRC_PKG = os.path.join(_REPO_ROOT, "approval_voice")
_SAMPLE_QUEUE = os.path.join(_REPO_ROOT, "examples", "announce_queue.json")

# openhome_ability/ から zip ルートへ持っていく実行時ファイル
_ABILITY_FILES = ("main.py", "background.py", "__init__.py", "requirements.txt")

_DEFAULT_ZIP = os.path.join(_REPO_ROOT, "dist", "approval-voice-ability.zip")


def _copy_package(src_pkg: str, dst_pkg: str) -> None:
    """approval_voice/ を __pycache__ を除いてコピーする。"""
    shutil.copytree(
        src_pkg,
        dst_pkg,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def stage(stage_root: str) -> None:
    """zip 化前のステージングディレクトリを構築する。"""
    os.makedirs(stage_root, exist_ok=True)
    for fname in _ABILITY_FILES:
        src = os.path.join(_SRC_ABILITY, fname)
        if not os.path.isfile(src):
            raise FileNotFoundError(f"required ability file missing: {src}")
        shutil.copy2(src, os.path.join(stage_root, fname))
    _copy_package(_SRC_PKG, os.path.join(stage_root, "approval_voice"))


def py_compile_tree(root: str) -> None:
    """root 配下の全 .py を構文チェックする（.pyc を残さない）。"""
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                src = os.path.join(dirpath, f)
                # cfile=os.devnull: 構文チェックだけして bytecode を残さない
                py_compile.compile(src, cfile=os.devnull, doraise=True)


def make_zip(stage_root: str, zip_path: str, arc_prefix: str = "") -> None:
    """stage_root の中身を zip 化する。arc_prefix を付けると配下に入れる。"""
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirs, files in os.walk(stage_root):
            # Never ship bytecode caches (py_compile may have created them).
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in sorted(files):
                if f.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, stage_root)
                arcname = os.path.join(arc_prefix, rel) if arc_prefix else rel
                zf.write(full, arcname.replace(os.sep, "/"))


def _write_src_stub(stub_root: str) -> None:
    """OpenHome ランタイム `src.*` の最小スタブを作る（import 検証専用）。

    background.py / main.py は `from src.agent.capability import MatchingCapability`
    等を import する。実ランタイムは本リポに無いので、import が通る最小の
    クラスだけを定義したスタブを置く。approval_voice は **同梱の本物**を使わせる
    （スタブ化しない）ので、検証対象である「bundle 内 import パス解決」を損なわない。
    """
    agent_dir = os.path.join(stub_root, "src", "agent")
    os.makedirs(agent_dir, exist_ok=True)
    open(os.path.join(stub_root, "src", "__init__.py"), "w").close()
    open(os.path.join(agent_dir, "__init__.py"), "w").close()
    with open(os.path.join(agent_dir, "capability.py"), "w", encoding="utf-8") as f:
        f.write("class MatchingCapability:\n    pass\n")
    with open(os.path.join(agent_dir, "capability_worker.py"), "w", encoding="utf-8") as f:
        f.write("class CapabilityWorker:\n    def __init__(self, *a, **k):\n        pass\n")
    with open(os.path.join(stub_root, "src", "main.py"), "w", encoding="utf-8") as f:
        f.write("class AgentWorker:\n    pass\n")


def verify_zip(zip_path: str, arc_prefix: str = "") -> None:
    """zip を展開し、別プロセスで実機 ability を import + 実データ経路を実証する。"""
    with tempfile.TemporaryDirectory() as work:
        extract = os.path.join(work, "extract")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract)

        # background.py / main.py のあるディレクトリ（= cwd にしたい場所）
        run_dir = os.path.join(extract, arc_prefix) if arc_prefix else extract
        for must in ("background.py", "main.py", "approval_voice"):
            if not os.path.exists(os.path.join(run_dir, must)):
                raise AssertionError(f"{must} not found under {run_dir}")

        # OpenHome ランタイム src.* のスタブ（approval_voice はスタブしない）
        stub_root = os.path.join(work, "stub")
        _write_src_stub(stub_root)

        code = textwrap.dedent(
            """
            import sys
            sys.path.insert(0, %(stub)r)   # src.* スタブ
            sys.path.insert(0, %(run)r)    # bundle ルート（background.py も同じ場所を積む）

            # 1) 実機 ability を import（from approval_voice... が解決すること）
            import background
            import main
            assert hasattr(background, "ApprovalVoiceWatcher")
            assert hasattr(main, "ApprovalVoiceStatus")

            # 2) 実データ経路（4 ゲートサンプル）を流す
            from approval_voice.bridge import load_queue
            from approval_voice.poller import ReadCursor
            from approval_voice.renderer import render_speech
            items = load_queue(%(sample)r)
            fresh = ReadCursor().unread(items)
            spoken = [render_speech(i) for i in fresh]
            assert len(spoken) == 4, len(spoken)
            print("VERIFY_OK", len(spoken))
            """
        ) % {"stub": stub_root, "run": run_dir, "sample": _SAMPLE_QUEUE}

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=run_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or "VERIFY_OK" not in proc.stdout:
            raise AssertionError(
                "zip import/path verify FAILED\n"
                f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            )
        print(f"[verify] ability import + data path OK ({proc.stdout.strip()})")


def build(zip_path: str, root_folder: str = "", verify: bool = True) -> str:
    arc_prefix = root_folder.strip("/") if root_folder else ""
    with tempfile.TemporaryDirectory() as tmp:
        stage_root = os.path.join(tmp, "stage")
        stage(stage_root)
        py_compile_tree(stage_root)
        print(f"[stage] py_compile OK ({stage_root})")
        make_zip(stage_root, zip_path, arc_prefix)
        print(f"[zip] wrote {zip_path}")
    if verify:
        verify_zip(zip_path, arc_prefix)
    _print_manifest(zip_path)
    return zip_path


def _print_manifest(zip_path: str) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    print("[contents]")
    for n in sorted(names):
        print(f"  {n}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", default=_DEFAULT_ZIP, help="出力 zip パス")
    p.add_argument(
        "--root-folder",
        default="",
        help="全ファイルを指定フォルダ配下に入れる（既定: ルート直置き）",
    )
    p.add_argument("--no-verify", action="store_true", help="展開→import 検証を省略")
    args = p.parse_args(argv)
    build(args.out, root_folder=args.root_folder, verify=not args.no_verify)
    print("\nDONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
