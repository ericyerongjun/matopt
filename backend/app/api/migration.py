"""Migration API (contract in docs/CODEX-INTEGRATION.md, Phase 2).

``detect`` scans the backend user's home and the repository root; ``import``
takes only item ids from a previous detection, never paths, so a client cannot
make the backend copy arbitrary files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.agent.migration import MigrationTargets, detect, import_items

router = APIRouter(prefix="/api", tags=["migration"])


@dataclass
class MigrationContext:
    home: Path
    project_dirs: list[Path]
    targets: MigrationTargets


def get_migration_context() -> MigrationContext:
    from app.config import REPO_ROOT, settings

    home = Path.home()
    return MigrationContext(home, [REPO_ROOT], MigrationTargets.from_settings(settings, home / ".matopt"))


class ImportRequest(BaseModel):
    items: list[str]


@router.get("/migration/detect")
async def detect_items(ctx: MigrationContext = Depends(get_migration_context)):
    return {"items": detect(ctx.home, ctx.project_dirs, ctx.targets)}


@router.post("/migration/import")
async def import_selected(body: ImportRequest, ctx: MigrationContext = Depends(get_migration_context)):
    results = import_items(body.items, home=ctx.home, project_dirs=ctx.project_dirs, targets=ctx.targets)
    restart = any(r["status"] == "imported" for r in results)
    return {"results": results, "restart_required": restart}
