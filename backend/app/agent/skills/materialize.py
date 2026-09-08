"""Copy an activated skill into the conversation workspace so scripts can run."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.agent.skills.loader import Skill
from app.agent.workspace import Workspace

IGNORE = shutil.ignore_patterns("__pycache__", ".git", ".DS_Store", "*.pyc")


def materialize_skill(skill: Skill, workspace: Workspace) -> Path:
    dest = workspace.root / "skills" / skill.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(skill.path, dest, ignore=IGNORE)
    return dest
