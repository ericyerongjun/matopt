"""OS sandbox for the processes an agent action spawns, ported from Codex.

``child_env`` stops a child from *inheriting* MatOpt's credentials; it does
not stop a child from *reading* them, or anything else the backend user can
reach, by absolute path. Codex wraps every command in a platform sandbox
(``codex-rs/sandboxing``): Seatbelt on macOS, bubblewrap on Linux, driven by
one ``SandboxPolicy`` (read-only / workspace-write / full access, writable
roots, network on or off). This module is that policy for MatOpt's ``bash``,
``lean_check`` and the Jupyter kernel.

Modes (``settings.sandbox_mode``):

``off``              no wrapper (the historical behaviour, and the default).
``workspace-write``  children write only the workspace, the kernel's private
                     directory and the temp directory; reads of MatOpt's data
                     directory (other conversations, the database), its
                     ``.env`` and the backend user's credential folders are
                     denied; network follows ``settings.sandbox_network``.
``read-only``        as above, but nothing outside temp may be written.
``external``         the deployment already isolates (a container per
                     workspace); nothing is wrapped and children are told so.

Backends: ``seatbelt`` runs ``/usr/bin/sandbox-exec`` by absolute path (never
from ``PATH``), ``bwrap`` runs bubblewrap with a read-only bind of ``/``, the
writable roots bound read-write, the data directory masked by a tmpfs with the
workspace bound back over it, fresh PID/IPC/user namespaces and, without
network, a fresh network namespace. ``auto`` picks by platform. If the chosen
backend is missing, wrapping fails closed: the tool reports that the sandbox
is unavailable rather than running unsandboxed.

The Seatbelt profile differs from Codex's on purpose. Codex starts from
``(deny default)`` and allows what a coding agent needs; MatOpt's kernel loads
NumPy, SciPy, Matplotlib, PyTorch and fonts, and that profile would need long
tuning to keep all of them working. MatOpt starts from ``(allow default)`` and
denies writes outside the roots, reads of the protected paths and outbound
network — rules later in a profile win over earlier ones, which the profile
relies on. It is a filesystem and network boundary, not process isolation;
the bubblewrap backend and a per-workspace container go further.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

SandboxMode = Literal["off", "workspace-write", "read-only", "external"]
SEATBELT = "/usr/bin/sandbox-exec"

# The backend user's credential stores, relative to their real home directory.
CREDENTIAL_PATHS = (".ssh", ".aws", ".gnupg", ".netrc", ".git-credentials", ".docker", ".kube",
                    ".config/gh", ".config/gcloud", ".azure", ".pypirc", ".matopt", "Library/Keychains")


class SandboxUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxPolicy:
    mode: SandboxMode = "off"
    backend: str = "none"
    writable_roots: tuple[Path, ...] = ()
    deny_read: tuple[Path, ...] = ()
    # Re-allowed beneath a denied path (the workspace inside the data directory).
    allow_read: tuple[Path, ...] = ()
    network: bool = True
    # Unix sockets a child must reach even with network off: the kernel's
    # code-mode tools socket (app/agent/kernel_tools.py). Listed by exact path
    # so the allowance survives if the blanket unix-socket rule is narrowed.
    unix_sockets: tuple[Path, ...] = ()

    @property
    def active(self) -> bool:
        return self.mode in ("workspace-write", "read-only") and self.backend != "none"

    def child_env(self) -> dict[str, str]:
        """Variables that tell a child it is sandboxed (Codex's CODEX_SANDBOX*)."""
        if self.mode == "off":
            return {}
        env = {"MATOPT_SANDBOX": self.backend if self.active else self.mode}
        if not self.network:
            env["MATOPT_SANDBOX_NETWORK_DISABLED"] = "1"
        return env


def _resolve(path: Path | str) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path).expanduser().absolute()


def _temp_roots() -> list[Path]:
    roots = {_resolve(tempfile.gettempdir())}
    if os.path.isdir("/tmp"):
        roots.add(_resolve("/tmp"))
    return sorted(roots)


def select_backend(preference: str) -> str:
    if preference in ("seatbelt", "bwrap", "none"):
        return preference
    if sys.platform == "darwin":
        return "seatbelt"
    if sys.platform.startswith("linux"):
        return "bwrap"
    return "none"


def build_policy(settings: Any, workspace_root: Path, *, extra_writable: Sequence[Path] = (),
                 extra_readable: Sequence[Path] = ()) -> SandboxPolicy:
    mode = getattr(settings, "sandbox_mode", "off")
    if mode not in ("workspace-write", "read-only"):
        return SandboxPolicy(mode=mode, network=bool(getattr(settings, "sandbox_network", True)))
    workspace = _resolve(workspace_root)
    writable = [*_temp_roots(), *(_resolve(p) for p in extra_writable)]
    if mode == "workspace-write":
        writable.insert(0, workspace)
    home = _resolve(os.environ.get("HOME") or Path.home())
    deny = [_resolve(settings.data_dir)]
    backend_env = Path(__file__).resolve().parents[2] / ".env"
    deny.append(backend_env)
    deny += [home / rel for rel in CREDENTIAL_PATHS]
    allow = [workspace, *(_resolve(p) for p in extra_readable)]
    return SandboxPolicy(
        mode=mode, backend=select_backend(getattr(settings, "sandbox_backend", "auto")),
        writable_roots=tuple(dict.fromkeys(writable)), deny_read=tuple(dict.fromkeys(deny)),
        allow_read=tuple(dict.fromkeys(allow)), network=bool(getattr(settings, "sandbox_network", True)))


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sbpl_string(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def seatbelt_profile(policy: SandboxPolicy) -> str:
    lines = ["(version 1)", "(allow default)",
             # Codex's fixed denies: no XPC service lookups by prefix tricks, and
             # no F_SETPROTECTIONCLASS / F_TRANSFEREXTENTS fcntls.
             "(deny system-fcntl (fcntl-command 80 110))"]
    for path in policy.deny_read:
        lines.append(f"(deny file-read* (subpath {_sbpl_string(path)}))")
    for path in policy.allow_read:
        lines.append(f"(allow file-read* (subpath {_sbpl_string(path)}))")
    lines.append("(deny file-write*)")
    lines.append('(allow file-write* (literal "/dev/null") (literal "/dev/zero") (literal "/dev/dtracehelper")'
                 ' (regex #"^/dev/tty") (regex #"^/dev/fd/") (literal "/dev/ptmx"))')
    for root in policy.writable_roots:
        lines.append(f"(allow file-write* (subpath {_sbpl_string(root)}))")
    # Protected paths stay unwritable even inside a writable root (the data
    # directory may live under the temp directory); the workspace is
    # re-allowed after them, since later rules win.
    for path in policy.deny_read:
        lines.append(f"(deny file-write* (subpath {_sbpl_string(path)}))")
    for root in policy.writable_roots:
        if any(_within(root, path) for path in policy.deny_read):
            lines.append(f"(allow file-write* (subpath {_sbpl_string(root)}))")
    # A writable root's own directory cannot be removed or renamed away.
    for root in policy.writable_roots:
        lines.append(f"(deny file-write-unlink (literal {_sbpl_string(root)}))")
    if not policy.network:
        lines.append("(deny network-outbound)")
        lines.append("(deny network-bind)")
        # The kernel talks to the backend over Unix sockets in its private dir.
        lines.append('(allow network-outbound (remote unix-socket))')
        lines.append('(allow network-bind (local unix-socket))')
        for sock in policy.unix_sockets:
            lines.append(f"(allow network-outbound (remote unix-socket (path-literal {_sbpl_string(sock)})))")
    return "\n".join(lines) + "\n"


def bwrap_argv(policy: SandboxPolicy, bwrap: str = "bwrap") -> list[str]:
    argv = [bwrap, "--new-session", "--die-with-parent", "--ro-bind", "/", "/", "--dev", "/dev",
            "--proc", "/proc", "--unshare-user", "--unshare-pid", "--unshare-ipc"]
    if not policy.network:
        argv.append("--unshare-net")
    for path in policy.deny_read:
        if path.is_dir():
            argv += ["--tmpfs", str(path)]
        elif path.exists():
            argv += ["--ro-bind", "/dev/null", str(path)]
    # Bound after the masks, so the workspace reappears inside the data dir.
    for path in policy.allow_read:
        if path.exists() and path not in policy.writable_roots:
            argv += ["--ro-bind", str(path), str(path)]
    for root in policy.writable_roots:
        if root.exists():
            argv += ["--bind", str(root), str(root)]
    return argv + ["--"]


def wrap_argv(argv: Sequence[str], policy: SandboxPolicy) -> list[str]:
    """``argv`` run under the policy's backend, or unchanged when inactive."""
    if not policy.active:
        return list(argv)
    if policy.backend == "seatbelt":
        if not os.path.exists(SEATBELT):
            raise SandboxUnavailable("Seatbelt (/usr/bin/sandbox-exec) is not available on this host.")
        return [SEATBELT, "-p", seatbelt_profile(policy), "--", *argv]
    if policy.backend == "bwrap":
        found = shutil.which("bwrap")
        if found is None:
            raise SandboxUnavailable("bubblewrap (bwrap) is not installed; install it or set SANDBOX_MODE=off.")
        return bwrap_argv(policy, found) + list(argv)
    raise SandboxUnavailable(f"Unknown sandbox backend {policy.backend!r}.")
