"""Preserve MCP structured and multimodal results without flooding model text."""

from __future__ import annotations

import base64
import json
import mimetypes
import uuid
from typing import Any

from app.agent.events import Artifact, ToolResult
from app.agent.session.events import snapshot_json
from app.agent.tools.base import ToolContext, truncate_text

MAX_RESULT_BYTES = 32 * 1024 * 1024


def field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        candidate = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if candidate is not None:
            return candidate
    return default


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return snapshot_json(value)


def convert_result(result: Any, ctx: ToolContext, max_output_chars: int) -> ToolResult:
    content = [json_value(block) for block in field(result, "content", default=[]) or []]
    structured = field(result, "structured_content", "structuredContent")
    if structured is None:
        data = field(result, "data")
        if isinstance(data, (dict, list, str, int, float, bool)):
            structured = data
    if structured is not None:
        structured = json_value(structured)
    packet = {"content": content, "structured_content": structured}
    encoded = json.dumps(packet, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        return ToolResult(ok=False, error="MCP result exceeds the 32 MiB result limit. Request a narrower result.",
                          meta={"code": "MCP_RESULT_TOO_LARGE", "received_bytes": len(encoded)})
    text_parts = [block["text"] for block in content
                  if block.get("type") == "text" and isinstance(block.get("text"), str)]
    if structured is not None:
        text_parts.append(json.dumps(structured, ensure_ascii=False))
    artifacts: list[Artifact] = []
    preserved = []
    token = uuid.uuid4().hex[:16]
    for index, block in enumerate(content):
        kind = block.get("type")
        mime = block.get("mimeType") or "application/octet-stream"
        resource = block.get("resource") if kind == "resource" else None
        data = block.get("data") if kind in {"image", "audio"} else None
        if isinstance(resource, dict):
            mime = resource.get("mimeType") or mime
            data = resource.get("blob")
        if isinstance(data, str):
            raw = base64.b64decode(data, validate=True)
            extension = mimetypes.guess_extension(mime) or ".bin"
            path = ctx.workspace.resolve(f"mcp/{token}-{index}{extension}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            artifact = ctx.workspace.artifact_for(path)
            artifact.mime = mime
            artifacts.append(artifact)
            preserved.append({"type": kind, "mimeType": mime, "path": artifact.path,
                              **({"uri": resource.get("uri")} if isinstance(resource, dict) else {})})
            text_parts.append(f"[{kind} saved: {artifact.path}]")
        else:
            preserved.append(block)
            if isinstance(resource, dict) and isinstance(resource.get("text"), str):
                text_parts.append(resource["text"])
            elif kind == "resource_link":
                text_parts.append(f"[resource: {block.get('name', '')} {block.get('uri', '')}]")
    # The full envelope remains inspectable even when text or structured JSON
    # exceeds the prompt budget. Inline media is kept as workspace artifacts.
    metadata: dict[str, Any] = {"mcp_content": preserved, "structured_content": structured}
    if len(encoded) > max_output_chars:
        path = ctx.workspace.resolve(f"mcp/{token}-result.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        artifact = ctx.workspace.artifact_for(path)
        artifacts.append(artifact)
        metadata = {"mcp_result_path": artifact.path,
                    "mcp_content": [{"type": block.get("type"), **({"path": block["path"]} if "path" in block else {})}
                                    for block in preserved]}
        text_parts.append(f"[complete MCP result saved: {artifact.path}]")
    is_error = bool(field(result, "is_error", "isError", default=False))
    output = truncate_text("\n".join(text_parts).strip() or "(no output)", max_output_chars)
    return ToolResult(ok=not is_error, output=output, artifacts=artifacts,
                      error="MCP tool reported an error" if is_error else None, meta=metadata)
