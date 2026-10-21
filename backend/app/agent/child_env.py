"""
The environment every child process spawned for an agent action receives.

Why this module exists
----------------------
``bash`` (``tools/shell_tool.py``) and ``python`` (the Jupyter kernel in
``kernel.py``) used to inherit the backend's ambient environment verbatim —
``env = dict(os.environ)``. That environment holds MatOpt's *own* credentials:
``DEEPSEEK_API_KEY``, ``OPENAI_API_KEY``, ``QWEN_API_KEY``, ``TAVILY_API_KEY``,
``BRAVE_SEARCH_API_KEY``, ``WOLFRAM_APP_ID``. ``docker-compose.yml`` passes
``backend/.env`` in with ``env_file:``, so in a deployment those are real
process-environment variables, and the agent could read its own API keys with
a one-line ``python`` cell. Prompt injection is sufficient to cause it; no
sandbox is needed to make that a problem today.

Children therefore get an explicit *allowlist* rather than an inheritance: a
name reaches the child because it is listed here with a reason, not because it
happened to be set. Anything a specific tool needs beyond the list is passed
by that tool through ``extra``, which keeps the credential surface auditable
in one file.

The design follows dsh's subprocess environment scrub
(``packages/subprocess/subprocess/src/index.ts`` — ``SENSITIVE_ENV_PATTERN``
is ``/KEY|PASSWORD|SECRET|TOKEN/i``, and the ambient environment is scrubbed of
both credential-shaped names and the harness's own namespaced variables),
with the allowlist §3.9.3 of ``docs/DSH-REFINEMENT-PLAN.md`` asks for instead
of a deny-list scrub: a deny-list catches the common case, an allowlist is
what actually stops the leak. ``SENSITIVE_ENV_PATTERN`` is kept as a
belt-and-braces assertion over the *finished* dict, so a future allowlist
entry — or a caller's ``extra`` — that looks like a credential fails loudly
instead of shipping.

``HOME``: the agent's ``~`` is the workspace
--------------------------------------------
When ``workspace_root`` is given, ``HOME`` points at the per-conversation
workspace instead of the backend user's home directory. That is deliberate:

- ``HOME`` is the one leak an allowlist cannot close by omission. ``HOME`` has
  to be set at all — pip, matplotlib, IPython and git all write state under it
  and behave badly without it — and the moment it is the real home,
  ``~/.ssh/id_rsa``, ``~/.aws/credentials``, ``~/.netrc``,
  ``~/.git-credentials``, ``~/.docker/config.json`` and ``~/.config/gh`` are
  one short shell command away. Redirecting it removes the whole ``~/.*``
  credential family from the agent's reach in one line.
- It makes ``~`` agree with every other surface the agent sees: ``cwd`` is the
  workspace, the file tools are confined to the workspace, and now ``~`` is
  the workspace too, so a path that works in one place works in the others.
- Dot-directories created under the workspace root (``.cache``, ``.ipython``,
  ``.jupyter``) are skipped by ``Workspace.iter_files`` and
  ``is_internal_output``, so caches do not show up as artifacts or pollute
  the file listing.

Two costs, both accepted. Caches are no longer shared between conversations,
so the first ``pip install`` and the first matplotlib import in a fresh
workspace redo their work (seconds, not minutes). And the agent cannot see the
user's shell dotfiles or ``~/.gitconfig``; for a maths workbench whose
workspaces are private per conversation that is the right trade. A deployment
that disagrees can pass ``HOME`` explicitly through ``extra``.

This is *not* a containment boundary, and must not be described as one: there
is no sandbox, so an absolute path (``/Users/<user>/.ssh/id_rsa``) still reads
whatever the host user can read. Tier 1 stops credential *inheritance* by the
agent's own processes; Tier 3's container is what would stop reads.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Mapping

from app.config import settings

# dsh's credential-shaped-name pattern (packages/subprocess/subprocess/src/index.ts:47),
# used here as an assertion over the result rather than as the filter.
SENSITIVE_ENV_PATTERN = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.IGNORECASE)

# MatOpt's own namespace. Forwarded wholesale so deployment knobs reach children
# without an edit here for every new MATOPT_* name — but credential-shaped names
# inside it are still dropped, so the assertion below stays meaningful.
MATOPT_ENV_PREFIX = "MATOPT_"

# Names a child process may inherit from the backend's environment. Everything
# else is dropped; keep this list short and justify every entry.
ALLOWED_ENV_NAMES: frozenset[str] = frozenset({
    "PATH",                    # find the interpreter, pip, and shell helpers
    "HOME",                    # rewritten below when a workspace is known
    "LANG", "LC_ALL", "LC_CTYPE",   # text encoding for the C library
    "TZ",                      # timestamps in pandas/date-heavy analysis
    "TERM", "NO_COLOR",        # keep ANSI escapes out of tool output
    "PYTHONUNBUFFERED",        # forced below; streamed tool output needs it
    "PYTHONDONTWRITEBYTECODE",     # no __pycache__ litter in the workspace
    "MPLBACKEND",              # defaulted below; the kernel overrides it
})

# The matplotlib backend is per surface and lives here so the two call sites
# share one source of truth. A shell must not reach for a GUI backend it cannot
# open (Agg renders without a display); the kernel wants figures captured as
# display_data, which is what the inline backend does for both matplotlib and
# plotly.
DEFAULT_MPLBACKEND = "Agg"
KERNEL_MPLBACKEND = "module://matplotlib_inline.backend_inline"


def assert_no_sensitive_env(env: Mapping[str, str]) -> None:
    """Raise if any name in *env* looks like a credential.

    The allowlist already cannot forward ``DEEPSEEK_API_KEY``, but ``extra``
    lets a caller add a name and the allowlist itself may grow. A credential in
    a child environment is a silent, durable leak; failing the call is loud and
    gets fixed, so this check is deliberately fatal rather than a warning.
    """
    leaked = sorted(name for name in env if SENSITIVE_ENV_PATTERN.search(name))
    if leaked:
        raise RuntimeError(
            "Refusing to build a child environment with credential-shaped names: "
            + ", ".join(leaked)
            + ". Credentials belong behind a narrow, audited port, not in a spawned process."
        )


def _from_ambient() -> dict[str, str]:
    """The allowlisted subset of ``os.environ`` (plus MatOpt's own namespace)."""
    env: dict[str, str] = {}
    for name in ALLOWED_ENV_NAMES:
        value = os.environ.get(name)
        if value:                       # absent and empty are the same to a child
            env[name] = value
    for name, value in os.environ.items():
        if name.startswith(MATOPT_ENV_PREFIX) and not SENSITIVE_ENV_PATTERN.search(name):
            env[name] = value
    return env


def build_child_env(
    *,
    workspace_root: Path | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Environment for a child process that runs an agent action.

    ``workspace_root`` redirects ``HOME`` into the conversation workspace (see
    the module docstring for why). ``extra`` is merged last and therefore wins,
    which is how the kernel asks for its inline matplotlib backend.
    """
    if settings.child_env_allowlist:
        env = _from_ambient()
    else:
        # Escape hatch (settings.child_env_allowlist=False): the pre-Tier-1
        # behaviour, ambient environment included. The assertion below is
        # skipped here on purpose — running it would defeat the hatch.
        env = dict(os.environ)

    # The venv's bin directory goes first so `python`/`pip` in the child are the
    # same interpreter as the python tool, whatever the ambient PATH says.
    bindir = str(Path(sys.executable).parent)
    path = env.get("PATH", "")
    env["PATH"] = bindir + os.pathsep + path if path else bindir

    # Agent children are headless and stream their output; both are forced
    # rather than defaulted so an inherited value cannot silently defeat them.
    env["MPLBACKEND"] = DEFAULT_MPLBACKEND
    env["PYTHONUNBUFFERED"] = "1"

    if workspace_root is not None:
        home = Path(workspace_root)
        home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(home)

    if extra:
        env.update(extra)

    # A PYTHONHOME inherited from a conda/embedded interpreter makes the venv
    # python unable to find its own stdlib.
    env.pop("PYTHONHOME", None)

    if settings.child_env_allowlist:
        assert_no_sensitive_env(env)
    return env
