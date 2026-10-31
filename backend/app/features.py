"""Feature registry with lifecycle stages, ported from Codex (``codex-rs/features``).

Codex declares every optional behaviour once, as ``FeatureSpec{key, stage,
default_enabled}``, and reads toggles from the ``[features]`` table of its
config layers. MatOpt's switches were scattered ``*_enabled`` booleans in
``Settings``; this registry names them, gives each a stage and a description
for the Settings page, and says whether a change applies live or needs a
restart. The value itself stays the ``Settings`` field, so env, ``.env`` and
the user TOML (``config_layers.py``) all keep working.

Stages follow Codex: ``under_development`` (hidden), ``experimental``
(shown, opt-in), ``stable``, ``deprecated`` (shown with a warning) and
``removed`` (ignored, warns when set).

Security settings are deliberately not features. The sandbox mode, network
access, hooks and child-environment allowlist can only be set by the operator
(env or the TOML file): the agent's own kernel can reach the HTTP API, so an
API toggle for them would let an agent switch off its own containment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Stage = Literal["under_development", "experimental", "stable", "deprecated", "removed"]


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    setting: str
    stage: Stage
    name: str
    description: str
    live: bool = True            # applies without a restart


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("web_search", "web_search_enabled", "stable", "Web search",
                "Search and fetch the web for current information.", live=False),
    FeatureSpec("skill_auto_activate", "skill_auto_activate", "stable", "Automatic skills",
                "Activate matching Agent Skills without a slash command."),
    FeatureSpec("document_ocr", "document_ocr_enabled", "stable", "Document reading",
                "Read PDFs and scans with the vision model.", live=False),
    FeatureSpec("document_auto_parse", "document_auto_parse", "stable", "Read attachments automatically",
                "Parse attached documents before the first step."),
    FeatureSpec("data_agent", "data_agent_enabled", "stable", "Data agent",
                "Delegate substantial data analysis to the data agent.", live=False),
    FeatureSpec("speech", "speech_enabled", "stable", "Voice",
                "Local speech recognition and synthesis.", live=False),
    FeatureSpec("agents_md", "agents_md_enabled", "stable", "AGENTS.md instructions",
                "Follow AGENTS.md files from ~/.matopt, the data folder and the workspace."),
    FeatureSpec("align_monitor_llm", "align_monitor_llm", "experimental", "Model-reviewed tool calls",
                "Ask the model to review flagged tool calls (slower)."),
    FeatureSpec("redact_tool_output", "redact_tool_output", "stable", "Redact secrets in tool output",
                "Mask credential-shaped strings before the model sees them."),
    FeatureSpec("subagents", "subagents_enabled", "experimental", "Sub-agents",
                "Let the agent spawn helper agents with roles for parallel work."),
    FeatureSpec("memories", "memories_enabled", "experimental", "Memories",
                "Remember durable facts and preferences across conversations."),
    FeatureSpec("code_mode", "code_mode_enabled", "experimental", "Tools from Python",
                "Let Python cells call MatOpt tools through a `tools` object."),
)

BY_KEY = {spec.key: spec for spec in FEATURES}


def feature_states(settings: Any, origins: dict[str, str] | None = None) -> list[dict]:
    origins = origins or {}
    out = []
    for spec in FEATURES:
        if spec.stage in ("under_development", "removed"):
            continue
        out.append({"key": spec.key, "name": spec.name, "description": spec.description, "stage": spec.stage,
                    "enabled": bool(getattr(settings, spec.setting, False)), "live": spec.live,
                    "origin": origins.get(spec.setting, "default")})
    return out
