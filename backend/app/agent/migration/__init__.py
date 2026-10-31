"""Import MCP servers, skills, instructions and hooks from Claude Code, Cursor and
Codex (a subset of Codex's ``external-agent-migration``)."""

from app.agent.migration.detect import DetectedItem, MigrationTargets, detect, scan
from app.agent.migration.importer import import_items, rewrite_instructions

__all__ = ["DetectedItem", "MigrationTargets", "detect", "import_items", "rewrite_instructions", "scan"]
