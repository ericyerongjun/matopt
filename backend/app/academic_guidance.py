"""Shared academic standards, kept as literal Markdown to preserve LaTeX."""
from pathlib import Path


ACADEMIC_GUIDANCE = Path(__file__).with_suffix(".md").read_text(encoding="utf-8").strip()


def with_academic_guidance(role_prompt: str) -> str:
    """Apply the standards without changing a role's tools or output contract."""
    return (
        ACADEMIC_GUIDANCE
        + "\n\nApply these standards within the role below. Its evidence, tool, and output-format "
        "constraints remain binding. For a checking role, use the rigor standards while keeping "
        "its concise report format. For a final-writing role, preserve the supplied derivations "
        "and assumptions without inventing missing arguments or results.\n\n"
        + role_prompt
    )
