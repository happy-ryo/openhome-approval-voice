"""The deploy bundle must be OpenHome-sandbox-clean and minimal.

Builds the real deploy zip via deploy/build_zip.py — the single source of truth
for *what is bundled* — and asserts:
  1. build() succeeds (it runs py_compile + scan_bundle_clean, which raises if any
     forbidden import / raw open() / top-level json is present in a bundled .py,
     + the extract→import→codec data-path verify);
  2. the PC-side file I/O and the M2 mocks are excluded from the bundle;
  3. the entry-imported pure modules are present.

This is the local guard that "tests green" == "bundle would pass add-capability".
"""

import sys
import zipfile
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "deploy"))

import build_zip  # noqa: E402  (deploy/ is not a package; added to path above)

_EXCLUDED = (
    "approval_voice/fileio.py",   # PC-side file I/O (os/pathlib) — never on-device
    "approval_voice/ability.py",  # M2 mock
    "approval_voice/speak.py",    # speak() mock
)
_REQUIRED = (
    "main.py",
    "background.py",
    "approval_voice/__init__.py",
    "approval_voice/schema.py",
    "approval_voice/renderer.py",
    "approval_voice/poller.py",
    "approval_voice/codec.py",
    "approval_voice/transport.py",
    "approval_voice/bridge.py",
)


def test_bundle_builds_clean_and_minimal(tmp_path):
    zip_path = tmp_path / "approval-voice-ability.zip"
    # Raises AssertionError if the bundle is not sandbox-clean or import fails.
    build_zip.build(str(zip_path), verify=True)

    names = set(zipfile.ZipFile(zip_path).namelist())
    for excluded in _EXCLUDED:
        assert excluded not in names, f"{excluded} must NOT be in the on-device bundle"
    for needed in _REQUIRED:
        assert needed in names, f"{needed} missing from bundle"


def test_scan_rejects_a_forbidden_import(tmp_path):
    # Negative control: the scanner must actually fire on a planted violation.
    bad = tmp_path / "bad_pkg"
    (bad).mkdir()
    (bad / "evil.py").write_text("import os\n", encoding="utf-8")
    try:
        build_zip.scan_bundle_clean(str(bad))
    except AssertionError:
        return
    raise AssertionError("scan_bundle_clean failed to flag `import os`")
