"""Deliberately publish useful answer content while agent work continues."""
from app.agent.events import ToolResult
from app.agent.tools.base import Tool


class PublishResultTool(Tool):
    name = "publish_result"
    read_only = False
    concurrent_safe = False
    side_effect_scope = "none"
    description = (
        "Publish a useful partial answer for the user while substantial work continues. Use a stable id to "
        "update the same result. Content must be user-facing findings or an answer section, never private "
        "reasoning, agent messages, verifier discussions or raw logs. Use markdown for normal rendered text, "
        "tables and math; source only for literal source explicitly requested by the user. Results are provisional. "
        "Cite only observed evidence. Withdraw an incorrect result by its id. The final answer is delivered separately."
    )
    parameters = {"type": "object", "properties": {
        "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,80}$"},
        "content": {"type": "string", "maxLength": 50000},
        "kind": {"type": "string", "enum": ["markdown", "source"], "default": "markdown"},
        "evidence_refs": {"type": "array", "maxItems": 50, "items": {"type": "string", "minLength": 1, "maxLength": 500}},
        "status": {"type": "string", "enum": ["provisional", "withdrawn"], "default": "provisional"},
    }, "required": ["id", "content"], "additionalProperties": False}

    def is_provider_visible(self, ctx):
        return super().is_provider_visible(ctx) and ctx.state.get("result_publisher") is not None

    async def run(self, ctx, id, content, kind="markdown", evidence_refs=None, status="provisional"):
        publisher = ctx.state.get("result_publisher")
        if publisher is None:
            return ToolResult(ok=False, error="Result publication is unavailable in this context.")
        try:
            block = await publisher.record(id, content, kind, evidence_refs, status)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(ok=True, output="The public result was recorded. Continue remaining work; provide a complete final answer.",
                          meta={"result_block_id": block["id"], "revision": block["revision"]})
