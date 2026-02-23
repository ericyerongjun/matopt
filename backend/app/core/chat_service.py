# Get the service from OpenAI to handle chat interactions
"""
Chat service: orchestrates LLM calls with optional tool-calling
(math engine, Wolfram Alpha, Python sandbox).

The service constructs a system prompt encouraging LaTeX/Markdown output,
sends the conversation to the configured LLM, and handles tool-call loops.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.core.math_engine import math_engine, TOOL_DEFINITIONS
from app.schemas.chat import ChatRequest, ChatResponse, ToolCall

logger = logging.getLogger(__name__)

# ── System prompt ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are MatOpt, an expert mathematics assistant.

Rules:
1. Always render math using LaTeX (wrap inline math in $...$ and display math in $$...$$).
2. When you write code, use fenced code blocks with the language tag.
3. When a diagram would help, output a Mermaid code block (```mermaid).
4. You have access to tools: parse_latex, simplify, solve, differentiate, integrate,
   evaluate, wolfram_query, exec_python, compare_answers.
   Use them when you need verified symbolic computation or numerical evaluation.
5. After using a tool, incorporate its result naturally into your response.
6. If a problem is ambiguous, state your assumptions clearly.
7. Be concise but thorough. Show key steps.
"""


class ChatService:
    """
    Manages conversation flow with the LLM backend.

    Supports both blocking and streaming modes.
    Tool-calling is handled in an automatic loop: the LLM may request
    tools, which are executed by the MathEngine, and results fed back.
    """

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key or "dummy",
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout,
        )
        self.model = settings.openai_model

    # ── non-streaming ───────────────────────────────────────────────────

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Send a chat request and return the final response (after tool calls)."""
        messages = self._build_messages(request)
        tools = TOOL_DEFINITIONS if request.use_sympy else None
        all_tool_calls: list[ToolCall] = []

        # Tool-call loop (max 5 rounds to prevent infinite loops)
        for _ in range(5):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                timeout=settings.llm_timeout,
            )
            choice = response.choices[0]

            # If no tool calls, we're done
            if not choice.message.tool_calls:
                break

            # Process each tool call
            messages.append(choice.message.model_dump())
            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments)
                result = math_engine.call(tc.function.name, args)
                all_tool_calls.append(ToolCall(
                    name=tc.function.name,
                    arguments=args,
                    result=result.result,
                ))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result.result or result.error or "",
                })
        else:
            logger.warning("Tool-call loop hit max iterations")

        content = choice.message.content or ""
        usage = response.usage.model_dump() if response.usage else None

        return ChatResponse(
            id=str(uuid.uuid4()),
            content=content,
            tool_calls=all_tool_calls,
            usage=usage,
        )

    # ── streaming (SSE) ─────────────────────────────────────────────────

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """
        Yield SSE-formatted chunks for streaming responses.

        TODO: Add your streaming implementation here.
        For now, falls back to non-streaming and yields the full response.
        """
        response = await self.chat(request)
        yield f"data: {json.dumps({'content': response.content, 'done': True})}\n\n"

    # ── private helpers ─────────────────────────────────────────────────

    def _build_messages(self, request: ChatRequest) -> list[dict]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for msg in request.messages:
            messages.append({"role": msg.role, "content": msg.content})
        return messages


# Module-level singleton
chat_service = ChatService()