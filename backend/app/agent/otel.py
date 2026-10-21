"""Optional OpenTelemetry export of finished turns as GenAI spans.

The private session ledger already records every model request, tool call and
side task with timing and usage. When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set
and the optional SDK is installed (``requirements-otel.txt``), each finished
turn is replayed from the ledger into one span tree that Langfuse, Phoenix,
Jaeger or any OTLP backend can display:

    invoke_agent matopt            (INTERNAL)
      chat deepseek-flash          (CLIENT)  one per model request or side task
      execute_tool python          (INTERNAL) one per tool call

Attribute names follow the OpenTelemetry GenAI semantic conventions. Message
content, tool arguments and results are exported only when
``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true``; by default spans
carry names, timing, status and token counts only.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)
_tracer: Any = None
_configured = False


def _tracer_or_none():
    global _tracer, _configured
    if _configured:
        return _tracer
    _configured = True
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") and not os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set but the OpenTelemetry SDK is not installed; "
                       "pip install -r requirements-otel.txt")
        return None
    provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "matopt")}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("matopt.agent")
    return _tracer


def _ns(seconds: Optional[float]) -> Optional[int]:
    return int(seconds * 1e9) if isinstance(seconds, (int, float)) else None


def _usage_attributes(usage: dict | None) -> dict:
    usage = usage or {}
    out = {}
    for key, attr in (("prompt_tokens", "gen_ai.usage.input_tokens"),
                      ("completion_tokens", "gen_ai.usage.output_tokens"),
                      ("cache_read_tokens", "gen_ai.usage.cache_read.input_tokens")):
        if isinstance(usage.get(key), int):
            out[attr] = usage[key]
    return out


def _capture() -> bool:
    return os.environ.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "").lower() == "true"


def export_turn(conversation_id: str, events: list[dict], turn: int, tracer=None) -> int:
    """Emit spans for one finished turn; returns the number of spans created."""
    tracer = tracer or _tracer_or_none()
    if tracer is None or not turn:
        return 0
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, Status, StatusCode

    rows = [e for e in events if (e.get("data") or {}).get("turn") == turn
            or (e["type"] == "llm/side-task" and (e.get("data") or {}).get("turn") in (None, turn))]
    start = next((e for e in rows if e["type"] == "turn/start"), None)
    end = next((e for e in rows if e["type"] == "turn/end"), None)
    if start is None or end is None:
        return 0
    meta = start["data"]
    capture = _capture()
    root = tracer.start_span(
        "invoke_agent matopt", kind=SpanKind.INTERNAL, start_time=_ns(start["time"]),
        attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "matopt",
                    "gen_ai.provider.name": meta.get("provider") or "", "gen_ai.request.model": meta.get("model") or "",
                    "gen_ai.conversation.id": conversation_id, "matopt.turn": turn,
                    "matopt.reasoning": meta.get("reasoning") or ""})
    context = trace.set_span_in_context(root)
    count, calls = 1, {}
    for event in rows:
        data, kind = event.get("data") or {}, event["type"]
        if kind == "assistant/message" and data.get("started_at"):
            attributes = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": data.get("provider") or "",
                          "gen_ai.request.model": data.get("model") or "", "matopt.phase": data.get("phase") or "",
                          "matopt.step": data.get("step") or 0, **_usage_attributes(data.get("usage"))}
            if data.get("finish_reason"):
                attributes["gen_ai.response.finish_reasons"] = [data["finish_reason"]]
            if isinstance(data.get("ttft_ms"), (int, float)):
                attributes["matopt.ttft_ms"] = data["ttft_ms"]
            if capture:
                attributes["gen_ai.output.messages"] = json.dumps(data.get("message"), ensure_ascii=False)
            span = tracer.start_span(f"chat {data.get('model') or ''}".strip(), context=context, kind=SpanKind.CLIENT,
                                     start_time=_ns(data["started_at"]), attributes=attributes)
            span.end(end_time=_ns(data.get("completed_at") or event["time"]))
            count += 1
        elif kind == "llm/side-task" and data.get("status") in {"completed", "error", "aborted"} and data.get("started_at"):
            span = tracer.start_span(
                f"chat {data.get('model') or ''}".strip(), context=context, kind=SpanKind.CLIENT,
                start_time=_ns(data["started_at"]),
                attributes={"gen_ai.operation.name": "chat", "gen_ai.provider.name": data.get("provider") or "",
                            "gen_ai.request.model": data.get("model") or "", "matopt.side_task": data.get("task") or "",
                            **_usage_attributes(data.get("usage"))})
            if data["status"] != "completed":
                span.set_status(Status(StatusCode.ERROR, data["status"]))
            span.end(end_time=_ns(data.get("completed_at") or event["time"]))
            count += 1
        elif kind == "tool/call":
            calls[data.get("callId")] = event
        elif kind == "tool/result" and data.get("callId") in calls:
            call = calls.pop(data["callId"])
            name = (call["data"].get("resolved_name") or call["data"].get("name") or data.get("name") or "")
            attributes = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
                          "gen_ai.tool.call.id": data["callId"] or "", "gen_ai.tool.type": "function"}
            if capture:
                attributes["gen_ai.tool.call.arguments"] = str(call["data"].get("arguments") or "")
                attributes["gen_ai.tool.call.result"] = str(data.get("output") or data.get("error") or "")[:10_000]
            span = tracer.start_span(f"execute_tool {name}", context=context, kind=SpanKind.INTERNAL,
                                     start_time=_ns(call["time"]), attributes=attributes)
            if data.get("ok") is False:
                span.set_status(Status(StatusCode.ERROR, str(data.get("error") or "tool failed")[:200]))
            span.end(end_time=_ns(event["time"]))
            count += 1
    reason = (end["data"].get("reason") or {}).get("kind")
    root.set_attribute("matopt.end_reason", reason or "")
    if reason not in (None, "completed"):
        root.set_status(Status(StatusCode.ERROR, reason))
    root.end(end_time=_ns(end["time"]))
    return count
