#!/usr/bin/env python3
"""Build a deployable OpenHome capability zip for approval-voice (M3.1).

単一の真実源 (single source of truth):
- このスクリプトはリポジトリ最上位の `openhome_ability/`（実機 ability=
  main.py / background.py / __init__.py / requirements.txt）をラップフォルダ直下へ、
  純ロジック `approval_voice/`（schema/renderer/poller/bridge/storage/...）をその
  さらに下へ **そのまま同梱**する。重複実装は作らない。
- `background.py` / `main.py` は同梱した `approval_voice` を **相対 import**
  （`from .approval_voice...`）で解決する。よって ability バンドルは
  **ラップフォルダごとパッケージとしてロード**される必要があり、`approval_voice/`
  は `background.py` と同じ階層（ラップフォルダ内）に同梱する。`sys.path` 書き換えは
  しない（M3.1 sandbox 準拠。openhome-dev/abilities · dungeon-master-voice が
  `from .dm_personalities` で実証する loading 形）。これが本タスク最大の注意点。

zip レイアウト（既定 = ラップフォルダ `approvalvoice/`、相対 import に必須）::

    approval-voice-ability.zip
    └── approvalvoice/         ← ラップフォルダ（= パッケージ）
        ├── main.py            ← interactive entry（status 読み上げのみ・必須ファイル）
        ├── background.py      ← background daemon（storage polling→逐語 speak）
        ├── __init__.py        ← パッケージマーカ（相対 import に必須）
        ├── requirements.txt   ← stdlib only
        └── approval_voice/    ← background.py が相対 import する純ロジック（同梱必須）
            ├── __init__.py
            ├── schema.py  renderer.py  poller.py  bridge.py  storage.py
            ├── ability.py  speak.py

`--root-folder NAME` でラップフォルダ名を変えられる（既定 `approvalvoice`）。
相対 import はフォルダ名に依存しないため任意の名前で動く。空文字を渡すと
（フラット配置）相対 import が壊れるため verify が失敗する。

検証 (verify) - ローカルで sandbox 準拠と import 健全性を事前実証する:
1. **sandbox lint** (`deploy/sandbox_lint.py`): 同梱する全 .py を OpenHome の
   add-capability 禁止パターン（低レベル OS アクセス / module 直下の encode import /
   生のファイルオープン / 低レベル signal / pickle/exec/eval/print/assert 等）で
   走査し、違反があればビルドを止める（design.md §M3.1）。
2. py_compile で全 .py を構文チェック。
3. 生成 zip を新しい temp へ展開し、**別プロセス**で実機 ability を
   `import <wrap>.background` / `import <wrap>.main` として（= 実ランタイムと同じ
   パッケージ形で）import する。OpenHome ランタイム `src.*` は最小スタブを置き、
   `approval_voice` は **同梱した本物**を相対 import で解決させる。これで
   「ラップフォルダ配下で `from .approval_voice...` が解決する」＝import/パス不一致が
   無いことを実証する。
4. 続けて `examples/announce_queue.json`（4 ゲートサンプル）を実データ経路
   items_from_raw→ReadCursor→render_speech に流し、4 件レンダリングできること、
   および daemon の self-seed サンプル（approval_voice/sample.py）が bridge を通って
   4 件になることを確認する。

使い方::

    py -3 deploy/build_zip.py                 # dist/approval-voice-ability.zip を生成+検証
    py -3 deploy/build_zip.py --root-folder approvalvoice
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

# sandbox lint（同一 deploy/ ディレクトリ）。スクリプト実行時は deploy/ が sys.path[0]、
# パッケージ import 時は deploy.sandbox_lint で解決する。
try:
    from sandbox_lint import scan_paths
except ImportError:  # pragma: no cover - imported as a package
    from deploy.sandbox_lint import scan_paths

# --- paths ----------------------------------------------------------------
_DEPLOY_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_DEPLOY_DIR)
_SRC_ABILITY = os.path.join(_REPO_ROOT, "openhome_ability")
_SRC_PKG = os.path.join(_REPO_ROOT, "approval_voice")
_SAMPLE_QUEUE = os.path.join(_REPO_ROOT, "examples", "announce_queue.json")

# openhome_ability/ からラップフォルダ直下へ持っていく実行時ファイル
_ABILITY_FILES = ("main.py", "background.py", "__init__.py", "requirements.txt")

# 相対 import に必須なので既定でラップフォルダを付ける（design.md §M3.1）。
_DEFAULT_ROOT = "approvalvoice"
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


def sandbox_lint(stage_root: str) -> None:
    """同梱する全 .py を OpenHome add-capability 禁止パターンで走査する。"""
    violations = scan_paths([stage_root], root=stage_root)
    if violations:
        raise AssertionError(
            "sandbox compliance FAILED (these would be rejected by add-capability):\n  "
            + "\n  ".join(violations)
        )
    print("[sandbox] add-capability forbidden-pattern scan OK")


def py_compile_tree(root: str) -> None:
    """root 配下の全 .py を構文チェックする（bytecode はステージへ残さない）。

    .pyc は throwaway temp ディレクトリへ書かせ、ステージ（=zip 元）には
    残さない（zip に .pyc を混入させないため。make_zip 側でも除外する二重防御）。
    """
    with tempfile.TemporaryDirectory() as cache:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    src = os.path.join(dirpath, f)
                    cfile = os.path.join(cache, f + "c")
                    py_compile.compile(src, cfile=cfile, doraise=True)


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
    クラスだけを定義したスタブを置く。approval_voice は **同梱の本物**を相対 import で
    解決させる（スタブ化しない）ので、検証対象である「bundle 内 import パス解決」を
    損なわない。
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


def verify_zip(zip_path: str, arc_prefix: str) -> None:
    """zip を展開し、別プロセスで実機 ability を **パッケージ形で** import + 実データ
    経路を実証する。相対 import はバンドルがパッケージとしてロードされる前提なので、
    ラップフォルダ（arc_prefix）が必須。"""
    if not arc_prefix:
        raise AssertionError(
            "verify requires a wrap folder (--root-folder); relative imports "
            "(from .approval_voice...) need the bundle loaded as a package"
        )
    with tempfile.TemporaryDirectory() as work:
        extract = os.path.join(work, "extract")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract)

        pkg_dir = os.path.join(extract, arc_prefix)
        for must in ("background.py", "main.py", "__init__.py", "approval_voice"):
            if not os.path.exists(os.path.join(pkg_dir, must)):
                raise AssertionError(f"{must} not found under {pkg_dir}")

        # OpenHome ランタイム src.* のスタブ（approval_voice はスタブしない）
        stub_root = os.path.join(work, "stub")
        _write_src_stub(stub_root)

        code = textwrap.dedent(
            """
            import importlib
            import json
            import sys
            sys.path.insert(0, %(stub)r)     # src.* スタブ
            sys.path.insert(0, %(extract)r)  # ラップフォルダの親（= パッケージ探索ルート）

            pkg = %(pkg)r

            # 1) 実機 ability を実ランタイムと同じ「<wrap>.background / <wrap>.main」形で
            #    import（from .approval_voice... が相対解決すること）
            bg = importlib.import_module(pkg + ".background")
            mn = importlib.import_module(pkg + ".main")
            assert hasattr(bg, "ApprovalVoiceWatcher"), "ApprovalVoiceWatcher missing"
            assert hasattr(mn, "ApprovalVoiceStatus"), "ApprovalVoiceStatus missing"

            # 2) 実データ経路（4 ゲートサンプル）を流す
            bridge = importlib.import_module(pkg + ".approval_voice.bridge")
            poller = importlib.import_module(pkg + ".approval_voice.poller")
            renderer = importlib.import_module(pkg + ".approval_voice.renderer")
            with open(%(sample)r, encoding="utf-8") as f:
                data = json.load(f)
            items = bridge.items_from_raw(data)
            fresh = poller.ReadCursor().unread(items)
            spoken = [renderer.render_speech(i) for i in fresh]
            assert len(spoken) == 4, len(spoken)

            # 3) daemon の smoke autoseed サンプルも bridge を通って 4 件になる
            #    （公開衛生 + gate 検証）。daemon import 済 = sample/flag の相対 import も健全。
            sample = importlib.import_module(pkg + ".approval_voice.sample")
            seeded = bridge.notifications_to_payload(sample.SAMPLE_NOTIFICATIONS)
            assert len(seeded) == 4, len(seeded)
            assert isinstance(sample.SMOKE_AUTOSEED, bool)

            print("VERIFY_OK", len(spoken))
            """
        ) % {"stub": stub_root, "extract": extract, "pkg": arc_prefix, "sample": _SAMPLE_QUEUE}

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=extract,
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


def build(zip_path: str, root_folder: str = _DEFAULT_ROOT, verify: bool = True) -> str:
    arc_prefix = root_folder.strip("/") if root_folder else ""
    with tempfile.TemporaryDirectory() as tmp:
        stage_root = os.path.join(tmp, "stage")
        stage(stage_root)
        sandbox_lint(stage_root)
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
    p.add_argument("--out", default=_DEFAULT_ZIP, help="output zip path")
    p.add_argument(
        "--root-folder",
        default=_DEFAULT_ROOT,
        help="wrap-folder name (default: approvalvoice; required for relative imports)",
    )
    p.add_argument("--no-verify", action="store_true", help="skip extract->import verify")
    args = p.parse_args(argv)
    build(args.out, root_folder=args.root_folder, verify=not args.no_verify)
    print("\nDONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
