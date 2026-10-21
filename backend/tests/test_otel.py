"""Finished turns replay from the session ledger into one GenAI span tree."""
import pytest

pytest.importorskip("opentelemetry.sdk")
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from app.agent import otel  # noqa: E402
from app.agent.events import ToolResult  # noqa: E402
from app.agent.tools.base import Tool  # noqa: E402
from app.storage.db import Database  # noqa: E402
from tests.fake_provider import FakeProvider  # noqa: E402
from tests.test_session_harness import collect, make_harness  # noqa: E402


class Echo(Tool):
    name = "echo"
    description = "echo"
    parameters = {"type": "object", "properties": {"value": {"type": "string"}}}

    async def run(self, ctx, value="", **_):
        return ToolResult(ok=True, output=value)


@pytest.mark.asyncio
async def test_turn_exports_agent_chat_and_tool_spans_without_content_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", raising=False)
    db = Database(tmp_path / "test.sqlite")
    provider = FakeProvider([{"tool": "echo", "args": {"value": "secret"}}, {"text": "done"}, {"text": "done"}])
    harness = make_harness(db, provider, tmp_path, tools=[Echo()])
    events = await collect(harness, conversation_id=None, text="private question")
    cid = events[0]["conversation_id"]
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    count = otel.export_turn(cid, db.list_session_events(cid), 1, tracer=tracer_provider.get_tracer("test"))
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert count == len(exporter.get_finished_spans()) >= 3
    root = spans["invoke_agent matopt"]
    assert root.attributes["gen_ai.conversation.id"] == cid
    tool = spans["execute_tool echo"]
    assert tool.parent.span_id == root.context.span_id and tool.attributes["gen_ai.tool.call.id"]
    chat = next(s for s in exporter.get_finished_spans() if s.name.startswith("chat") and "matopt.step" in s.attributes)
    assert chat.attributes["gen_ai.usage.input_tokens"] == 10 and chat.parent.span_id == root.context.span_id
    assert not any("secret" in str(v) or "private question" in str(v)
                   for s in exporter.get_finished_spans() for v in s.attributes.values())
    db.close()


def test_export_is_disabled_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setattr(otel, "_configured", False)
    assert otel.export_turn("c", [], 1) == 0
