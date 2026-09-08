"""GET /api/skills — inspect the skill catalog the harness loaded."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.runtime import get_runtime

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
async def list_skills():
    rt = get_runtime()
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "source": s.source,
                "license": s.license,
                "files": s.files,
            }
            for s in rt.skills
        ]
    }


@router.get("/{name}")
async def get_skill(name: str):
    rt = get_runtime()
    skill = rt.skills.get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Unknown skill {name!r}")
    return {
        "name": skill.name,
        "description": skill.description,
        "source": skill.source,
        "license": skill.license,
        "compatibility": skill.compatibility,
        "metadata": skill.metadata,
        "allowed_tools": skill.allowed_tools,
        "files": skill.files,
        "body": skill.body,
        "path": str(skill.path),
    }
