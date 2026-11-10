"""OS sandbox for the processes an agent action spawns, ported from Codex.

``child_env`` stops a child from *inheriting* MatOpt's credentials; it does
not stop a child from *reading* them, or anything else the backend user can
reach, by absolute path. Codex wraps every command in a platform sandbox
(``codex-rs/sandboxing``): Seatbelt on macOS, bubblewrap on Linux, driven by
one ``SandboxPolicy`` (read-only / workspace-write / full access, writable
roots, network on or off). This module is that policy for MatOpt's ``bash``,
``lean_check`` and the Jupyter kernel.

Modes (``settings.sandbox_mode``):

``off``              no wrapper.
``workspace-write``  (the default) children write only the workspace, the kernel's private
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

import hashlib
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Sequence
from urllib.parse import urlsplit

SandboxMode = Literal["off", "workspace-write", "read-only", "external"]
SEATBELT = "/usr/bin/sandbox-exec"
# Parent of the per-conversation temp directories. Short on purpose: Python's
# multiprocessing puts Unix sockets under TMPDIR, limited to ~104 characters.
TEMP_PARENT = "mo-c"
logger = logging.getLogger(__name__)

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
    # Loopback ports serving MatOpt's own API, closed even with network on so a
    # child cannot call the unsandboxed backend. Seatbelt only: bubblewrap can
    # only drop all networking, so build_policy drops it unless API_AUTH_TOKEN
    # already stops a child from using the API.
    blocked_loopback_ports: tuple[int, ...] = ()
    # This conversation's temp directory, exported as TMPDIR.
    temp_dir: Optional[str] = None

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
        if self.temp_dir:
            env.update(TMPDIR=self.temp_dir, TMP=self.temp_dir, TEMP=self.temp_dir)
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


def _conversation_temp(workspace: Path) -> tuple[Path, Path]:
    """(parent of every conversation's temp directory, this conversation's)."""
    parent = Path(tempfile.gettempdir()) / TEMP_PARENT
    own = parent / hashlib.sha256(str(workspace).encode()).hexdigest()[:10]
    own.mkdir(parents=True, exist_ok=True, mode=0o700)
    return parent, own


def select_backend(preference: str) -> str:
    if preference in ("seatbelt", "bwrap", "none"):
        return preference
    if sys.platform == "darwin":
        return "seatbelt"
    if sys.platform.startswith("linux"):
        return "bwrap"
    return "none"


def _backend_ports(settings: Any) -> tuple[int, ...]:
    """The backend's port and the dev/proxy ports that forward to it."""
    ports = {getattr(settings, "port", None)}
    for origin in getattr(settings, "cors_origins", ()):
        try:
            ports.add(urlsplit(origin).port)
        except ValueError:
            continue
    return tuple(sorted(int(port) for port in ports if port))


def build_policy(settings: Any, workspace_root: Path, *, extra_writable: Sequence[Path] = (),
                 extra_readable: Sequence[Path] = ()) -> SandboxPolicy:
    mode = getattr(settings, "sandbox_mode", "off")
    if mode not in ("workspace-write", "read-only"):
        return SandboxPolicy(mode=mode, network=bool(getattr(settings, "sandbox_network", True)))
    workspace = _resolve(workspace_root)
    temp_parent, temp_dir = _conversation_temp(workspace)
    writable = [*_temp_roots(), _resolve(temp_dir), *(_resolve(p) for p in extra_writable)]
    if mode == "workspace-write":
        writable.insert(0, workspace)
    home = _resolve(os.environ.get("HOME") or Path.home())
    deny = [_resolve(settings.data_dir)]
    backend_env = Path(__file__).resolve().parents[2] / ".env"
    deny.append(backend_env)
    # Server definitions in mcp.json can carry bearer tokens.
    mcp_config = getattr(settings, "resolved_mcp_config_path", None)
    if callable(mcp_config):
        deny.append(_resolve(mcp_config()))
    deny.append(_resolve(temp_parent))
    deny += [home / rel for rel in CREDENTIAL_PATHS]
    allow = [workspace, _resolve(temp_dir), *(_resolve(p) for p in extra_readable)]
    backend = select_backend(getattr(settings, "sandbox_backend", "auto"))
    network = bool(getattr(settings, "sandbox_network", True))
    if backend == "bwrap" and network and not getattr(settings, "api_auth_token", None):
        # With networking on, a child shares the backend's loopback and could
        # call its open API: read other conversations, approve its own requests.
        logger.warning("Sandbox network is off: on Linux it needs API_AUTH_TOKEN so children cannot call the API.")
        network = False
    return SandboxPolicy(
        mode=mode, backend=backend,
        writable_roots=tuple(dict.fromkeys(writable)), deny_read=tuple(dict.fromkeys(deny)),
        allow_read=tuple(dict.fromkeys(allow)), network=network,
        blocked_loopback_ports=_backend_ports(settings), temp_dir=str(temp_dir))


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
    if policy.network:
        for port in policy.blocked_loopback_ports:
            lines.append(f'(deny network-outbound (remote ip "localhost:{port}"))')
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
    # Mount order matters: a later bind of /tmp would uncover a mask beneath it.
    masked = lambda root: any(_within(root, path) for path in policy.deny_read)
    for root in policy.writable_roots:
        if root.exists() and not masked(root):
            argv += ["--bind", str(root), str(root)]
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
        if root.exists() and masked(root):
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
