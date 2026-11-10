"""Live ``analysis.ipynb`` for the conversation workspace.

Every user cell that runs on the Jupyter kernel is appended so the notebook
is the shared sandbox artifact for MatOpt ``python`` and the data agent.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from app.agent.workspace import SANDBOX_NOTEBOOK, Workspace

logger = logging.getLogger(__name__)

NOTEBOOK_NAME = SANDBOX_NOTEBOOK
# A cancelled caller's thread can still be running when the next cell arrives.
_write_lock = threading.Lock()


def _quarantine(path: Path) -> None:
    """Keep an unreadable notebook for inspection; the leading dot hides it from artifacts."""
    target = path.with_name(f".{path.name}.corrupt-{int(time.time() * 1000)}")
    try:
        os.replace(path, target)
    except OSError:
        logger.warning("Could not set aside unreadable notebook %s", path, exc_info=True)
    else:
        logger.warning("Notebook %s was unreadable; kept as %s and started a new one", path, target.name)


def _write_atomic(nb: Any, path: Path) -> None:
    """A crash mid-write leaves the previous notebook intact, never a torn file."""
    import nbformat

    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            nbformat.write(nb, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def append_analysis_cell(workspace: Workspace, code: str, result: Any) -> None:
    """Append a code cell (and captured outputs) to ``analysis.ipynb``."""
    with _write_lock:
        _append_analysis_cell(workspace, code, result)


def _append_analysis_cell(workspace: Workspace, code: str, result: Any) -> None:
    source = (code or "").strip()
    if not source:
        return
    try:
        import nbformat
        from nbformat.v4 import new_code_cell, new_notebook, new_output
    except Exception:  # noqa: BLE001
        logger.debug("nbformat unavailable; skip analysis.ipynb", exc_info=True)
        return

    path = workspace.root / SANDBOX_NOTEBOOK
    if path.exists():
        try:
            nb = nbformat.read(path, as_version=4)
        except Exception:  # noqa: BLE001
            _quarantine(path)
            nb = new_notebook()
            nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    else:
        nb = new_notebook()
        nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}

    outputs = []
    stdout = getattr(result, "stdout", "") or ""
    if stdout:
        outputs.append(new_output("stream", name="stdout", text=stdout))
    stderr = getattr(result, "stderr", "") or ""
    if stderr:
        outputs.append(new_output("stream", name="stderr", text=stderr))
    if getattr(result, "traceback", ""):
        outputs.append(new_output(
            "error",
            ename=getattr(result, "error_name", None) or "Error",
            evalue=getattr(result, "error_value", None) or "",
            traceback=(result.traceback or "").splitlines(),
        ))
    for art in getattr(result, "artifacts", None) or []:
        if getattr(art, "kind", None) != "image" or not art.path:
            continue
        try:
            data = base64.b64encode(workspace.resolve(art.path).read_bytes()).decode()
            outputs.append(new_output("display_data", data={"image/png": data}, metadata={}))
        except Exception:  # noqa: BLE001
            continue

    count = getattr(result, "execution_count", None)
    cell = new_code_cell(source, outputs=outputs)
    if count is not None:
        cell["execution_count"] = count
    nb.cells.append(cell)
    _write_atomic(nb, path)
