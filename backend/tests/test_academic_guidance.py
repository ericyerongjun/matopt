"""Keep literal mathematics intact when composing role and workspace prompts."""
import pytest

from app.academic_guidance import ACADEMIC_GUIDANCE
from app.agent.prompts import INTERACTIVE_UI_PROMPT, TITLE_PROMPT, build_system_prompt, build_turn_context


@pytest.mark.parametrize("interactive_ui", [False, True])
def test_literal_guidance_survives_workspace_and_interactive_prompt_composition(interactive_ui):
    files = r"proof_{n}.tex: \frac{a+b}{c}"
    prompt = build_system_prompt(interactive_ui=interactive_ui)
    context = build_turn_context(files, "", skills_active="Check {unexpanded} assumptions.")
    assert prompt.count(ACADEMIC_GUIDANCE) == 1
    assert files in context and "Check {unexpanded} assumptions." in context
    assert files not in prompt
    assert r"\frac{}{}" in prompt
    assert "&= B \\\\\n&= C" in prompt
    assert (INTERACTIVE_UI_PROMPT in prompt) == interactive_ui
    if interactive_ui:
        assert '{"$state":"key"}' in prompt
    assert "max 6 words" in TITLE_PROMPT
    assert ACADEMIC_GUIDANCE not in TITLE_PROMPT
