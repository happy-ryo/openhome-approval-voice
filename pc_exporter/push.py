#!/usr/bin/env python3
"""PC-side PUSH transport: deliver the exported queue to the DevKit (Refs #7).

This is an alternative to server.py's HTTP pull. The on-device ability was found
to be unable to make outbound HTTP -- the OpenHome add-capability sandbox rejects
`urllib` (HTTP 400, denylisted; design.md M3.3.1 / M3.1-s.7). So the production
transport is inverted: instead of the DevKit GETting the queue, the PC PUSHES the
queue file onto the DevKit (design.md M3.3.1 fallback "push: scp/rsync"), and the
on-device ability just reads its own storage (no network module in the bundle).

  core.export_queue (state.db -> section 1.3 JSON, atomic local write)
    -> THIS module: scp/sftp the file to the DevKit target
    -> ability reads QUEUE_STORE (openhome_ability/background.py)

Transport is paramiko-based on purpose: a stdlib-only push would shell out to
`scp.exe`, which is not reliably present on Windows (the PC side here). paramiko
speaks SFTP directly in-process.

OPEN QUESTION (design.md M3.3.1, requires on-device investigation): whether the
`--target` path the PC writes to actually *is* the ability's `capability_worker`
storage location is UNVERIFIED. The OpenHome SDK reference documents that storage
by role ("user data storage, shared across abilities" for `in_ability_directory=
False`) but publishes no concrete on-disk path, and the ability cannot `open()`
an arbitrary path (sandbox-banned) to bridge a different location. This module is
transport-only: it puts the file wherever `--target` says. Confirming that target
maps onto the ability storage is the first on-device step (deploy/DEPLOY.md 4.3).

Idempotency (design.md M3.3.1 "resend / idempotent"): the brief says "skip if
same mtime", but `core.export_queue` rewrites the file (atomic os.replace) every
loop, so the LOCAL mtime always changes and a literal mtime check would never
skip. We therefore key idempotency on the file's **content digest**: a re-export
whose bytes are identical to what was last delivered is NOT re-pushed. A
content-equal-but-mtime-changed re-export is the common steady state, so this is
the meaningful skip. We additionally re-push when the remote file is gone or its
size no longer matches (e.g. a DevKit reboot wiped it) so a digest match never
strands the DevKit with no file.

One-way invariant (design.md 3.1): the push direction is PC -> DevKit only. This
module never reads anything back from the DevKit except a stat() used purely to
decide whether to re-push; it never pulls org state from the device. The ability
side has no send path at all (the outbound GET was removed).

All CLI / print / log strings here stay ASCII (cp932 console safety); queue
values (possibly Japanese) live only inside the utf-8 file bytes and are never
printed to the console.
"""
from __future__ import annotations

import hashlib
import posixpath
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

DEFAULT_SSH_PORT = 22


# --- target parsing -------------------------------------------------------
@dataclass
class Target:
    """A parsed push target.

    Remote: ``user@host:/remote/path`` (user optional) -> ``host`` set, ``path``
    is the remote path. Local: a path with no ``host:`` prefix -> ``host`` None,
    ``path`` is a local filesystem path (used for the loopback test and for a
    PC-local shared-folder / mounted drop, design.md M3.3.1 "shared mount").
    """

    host: Optional[str]
    user: Optional[str]
    path: str
    port: int = DEFAULT_SSH_PORT

    @property
    def is_local(self) -> bool:
        return self.host is None


def _looks_like_windows_drive(spec: str) -> bool:
    """True for ``C:\\x`` / ``C:/x`` / ``C:`` -- a local drive path, not host:path."""
    return (
        len(spec) >= 2
        and spec[1] == ":"
        and spec[0].isalpha()
        and (len(spec) == 2 or spec[2] in "/\\")
    )


def parse_target(spec: str, port: int = DEFAULT_SSH_PORT) -> Target:
    """Parse a push target spec into a :class:`Target`.

    Forms:
      - ``user@host:/data/approvalvoice/announce_queue.json`` (remote, user given)
      - ``host:/data/approvalvoice/announce_queue.json``      (remote, default user)
      - ``/tmp/drop/announce_queue.json`` or ``C:/drop/q.json`` (local)

    A Windows drive path (``C:\\...``) is treated as local even though it contains
    a colon. A spec with no colon at all is local. ``port`` applies to remote
    targets (SSH port; scp-style ``:port`` is intentionally not embedded in the
    spec to avoid ambiguity with the remote path colon).
    """
    if not isinstance(spec, str) or not spec:
        raise ValueError("empty push target")
    if _looks_like_windows_drive(spec) or ":" not in spec:
        return Target(host=None, user=None, path=spec, port=port)
    left, _, path = spec.partition(":")
    if not path:
        raise ValueError("push target %r has no remote path after ':'" % spec)
    if "@" in left:
        user, _, host = left.partition("@")
        if not user:
            raise ValueError("push target %r has an empty user before '@'" % spec)
    else:
        user, host = None, left
    if not host:
        raise ValueError("push target %r has no host" % spec)
    return Target(host=host, user=(user or None), path=path, port=port)


# --- transports -----------------------------------------------------------
class Transport(Protocol):
    """Minimal push transport surface (so the loopback test can substitute one)."""

    def remote_size(self, remote_path: str) -> Optional[int]:
        """Size in bytes of ``remote_path``, or None if it does not exist."""

    def put_atomic(self, local_path: Path, remote_path: str) -> None:
        """Copy ``local_path`` to ``remote_path`` atomically (temp + rename)."""

    def close(self) -> None:
        ...


class LocalTransport:
    """Filesystem transport: deliver to a local path (loopback test / shared mount).

    Used for the build-time loopback proof and for a PC-local drop directory (a
    mounted DevKit share, design.md M3.3.1). The write is atomic (temp file in the
    destination directory then ``os.replace``) so a concurrent reader never sees a
    half-written queue.
    """

    def remote_size(self, remote_path: str) -> Optional[int]:
        p = Path(remote_path)
        return p.stat().st_size if p.exists() else None

    def put_atomic(self, local_path: Path, remote_path: str) -> None:
        import os
        import tempfile

        dst = Path(remote_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".announce_push.", suffix=".tmp")
        os.close(fd)
        try:
            shutil.copyfile(str(local_path), tmp)
            os.replace(tmp, str(dst))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def close(self) -> None:
        return None


class SftpTransport:
    """paramiko SFTP transport (the real PC -> DevKit push).

    paramiko is imported lazily so importing this module (and running the local
    loopback test) does not require paramiko to be installed. Auth uses the SSH
    agent + default key files by default; ``key_filename`` / ``password`` override.
    """

    def __init__(
        self,
        host: str,
        user: Optional[str] = None,
        port: int = DEFAULT_SSH_PORT,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        try:
            import paramiko  # lazy: PC-only dep, not needed for local transport
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "paramiko is required for SSH push. Install it: pip install paramiko"
            ) from exc
        self._client = paramiko.SSHClient()
        self._client.load_system_host_keys()
        # Trust-on-first-use for an appliance on the same LAN that has no entry in
        # known_hosts yet. The link is a trusted home LAN; AutoAddPolicy avoids a
        # hard failure on first connect. Document this in DEPLOY.md.
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname=host,
            port=port,
            username=user,
            password=password,
            key_filename=key_filename,
            timeout=timeout,
            allow_agent=True,
            look_for_keys=True,
        )
        self._sftp = self._client.open_sftp()

    def remote_size(self, remote_path: str) -> Optional[int]:
        try:
            return self._sftp.stat(remote_path).st_size
        except IOError:
            # FileNotFoundError subclasses IOError; any stat failure -> treat as
            # absent so the caller re-pushes rather than assuming it is current.
            return None

    def put_atomic(self, local_path: Path, remote_path: str) -> None:
        tmp = remote_path + ".tmp"
        self._sftp.put(str(local_path), tmp)
        # Atomic swap on the remote so the ability never reads a half-written file.
        try:
            self._sftp.posix_rename(tmp, remote_path)
        except (IOError, AttributeError):
            # Server without posix-rename@openssh.com: best-effort remove + rename.
            try:
                self._sftp.remove(remote_path)
            except IOError:
                pass
            self._sftp.rename(tmp, remote_path)

    def close(self) -> None:
        try:
            self._sftp.close()
        finally:
            self._client.close()


def make_transport(target: Target, **ssh_kwargs) -> Transport:
    """Build the transport for ``target`` (LocalTransport for a local path)."""
    if target.is_local:
        return LocalTransport()
    return SftpTransport(
        host=target.host,  # type: ignore[arg-type]
        user=target.user,
        port=target.port,
        **ssh_kwargs,
    )


# --- push logic -----------------------------------------------------------
def _digest(path: Path) -> str:
    """SHA-256 of the file bytes (the idempotency key; see module docstring)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class PushState:
    """Carried across push attempts so a steady state is not re-pushed.

    ``last_digest`` is the content digest of the bytes last successfully
    delivered; ``last_size`` is their byte length (used to detect a remote that
    was wiped and now mismatches). Construct fresh per run.
    """

    last_digest: Optional[str] = None
    last_size: Optional[int] = None


def push_once(
    local_path: Path,
    remote_path: str,
    transport: Transport,
    state: PushState,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """Deliver ``local_path`` to ``remote_path`` unless it is already current.

    Returns True if a transfer happened, False if it was skipped as a no-op.
    Skips only when the content digest is unchanged AND the remote still holds a
    file of the matching size; otherwise (changed content, or remote gone/wrong
    size) it (re)pushes. Raises on transport failure -- the caller decides retry.
    """
    local_path = Path(local_path)
    digest = _digest(local_path)
    size = local_path.stat().st_size
    if state.last_digest == digest:
        remote = transport.remote_size(remote_path)
        if remote == size:
            if log:
                log("push: unchanged (digest match, remote present) -> skip")
            return False
        if log:
            log("push: content unchanged but remote missing/mismatched -> re-push")
    transport.put_atomic(local_path, remote_path)
    state.last_digest = digest
    state.last_size = size
    if log:
        log("push: delivered %d bytes to remote" % size)
    return True


def push_with_backoff(
    local_path: Path,
    remote_path: str,
    transport: Transport,
    state: PushState,
    attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """``push_once`` with bounded exponential backoff on transport failure.

    Retries up to ``attempts`` times (1s, 2s, 4s, ... capped at ``max_delay``) on
    any OSError/IOError/transport exception, re-raising the last error if every
    attempt fails. ``sleep`` is injectable so tests do not wait on real time.
    """
    delay = base_delay
    last_exc: Optional[BaseException] = None
    for i in range(1, attempts + 1):
        try:
            return push_once(local_path, remote_path, transport, state, log=log)
        except Exception as exc:  # noqa: BLE001 - transport/network failure
            last_exc = exc
            if log:
                log("push attempt %d/%d failed: %s" % (i, attempts, exc))
            if i == attempts:
                break
            sleep(min(delay, max_delay))
            delay *= 2
    assert last_exc is not None  # only reached after a failure
    raise last_exc
