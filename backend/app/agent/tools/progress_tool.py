"""An explicit channel for brief user-facing updates, separate from reasoning."""
from app.agent.events import ToolResult, ev
from app.agent.tools.base import Tool


class ReportProgressTool(Tool):
    name = "report_progress"
    # Like update_plan, this mutates durable presentation state. Keep updates
    # ordered even though the tool cannot alter workspace or system files.
    read_only = False
    concurrent_safe = False
    side_effect_scope = "none"
    description = ("Publish a brief update addressed to the user during substantial work. State an observable "
                   "action, a supported finding, or what happens next. Never include private reasoning, agent "
                   "messages, verifier reports, raw logs, or unverified conclusions. Use at meaningful milestones, "
                   "not every model step. The final answer is delivered separately.")
    parameters = {"type": "object", "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 400}},
                  "required": ["text"], "additionalProperties": False}

    async def run(self, ctx, text: str):
        if not isinstance(text, str) or not text.strip() or len(text) > 400:
            return ToolResult(ok=False, error="Use a brief public update of 1–400 characters.")
        await ctx.emit(ev("public_progress", text=text.strip()))
        return ToolResult(ok=True, output="The progress update was shown to the user.")
