"""Live ``analysis.ipynb`` for the conversation workspace.

Every user cell that runs on the Jupyter kernel is appended so the notebook
is the shared sandbox artifact for MatOpt ``python`` and LAMBDA.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.agent.workspace import SANDBOX_NOTEBOOK, Workspace

logger = logging.getLogger(__name__)

NOTEBOOK_NAME = SANDBOX_NOTEBOOK


def append_analysis_cell(workspace: Workspace, code: str, result: Any) -> None:
    """Append a code cell (and captured outputs) to ``analysis.ipynb``."""
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
    nbformat.write(nb, path)
