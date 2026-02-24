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
   evaluate, plot_function, wolfram_query, exec_python, compare_answers.
   Use them for verified computation and to generate plots, charts, and tables.
5. **Thinking**: Before solving non-trivial problems, show your reasoning approach briefly.
6. **Interactive charts/plots/graphs**: When the user asks for a chart, graph, or plot:
   - **Preferred**: Use the `plot_function` tool for standard function plots (it returns Plotly JSON automatically).
   - **Plotly JSON**: For custom or complex plots, include a fenced code block with language tag `plotly` containing
     valid JSON with `{ "data": [...], "layout": {...} }` in Plotly.js format. Example:
     ````
     ```plotly
     {"data":[{"x":[1,2,3],"y":[4,1,7],"type":"scatter","mode":"lines+markers","name":"Example"}],"layout":{"title":"My Plot","xaxis":{"title":"x"},"yaxis":{"title":"y"}}}
     ```
     ````
   - **Chart.js**: For bar charts, pie charts, radar, doughnut, etc., use a fenced code block with language tag `chartjs` containing
     valid JSON with `{ "type": "bar"|"pie"|"line"|..., "data": {...}, "options": {...} }`. Example:
     ````
     ```chartjs
     {"type":"bar","data":{"labels":["A","B","C"],"datasets":[{"label":"Values","data":[10,20,15],"backgroundColor":["#10a37f","#3b82f6","#f59e0b"]}]}}
     ```
     ````
   - **Never use matplotlib** for generating images. Always produce interactive Plotly or Chart.js JSON.
   - Use exec_python only for numeric computation, NOT for generating plots.
7. **Show code**: When you use exec_python, include the Python code in your response
   inside a fenced ```python block so the user can see what was computed.
8. After using a tool, incorporate its result naturally into your response.
9. If a problem is ambiguous, state your assumptions clearly.
10. Be concise but thorough. Show key steps in your mathematical reasoning.
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

    async def generate_suggestions(self, count: int = 4) -> list[str]:
        """
        Ask the LLM for short, diverse suggestions and return them as a list.
        """
        count = max(1, min(count, 8))
        prompt = (
            "Generate {count} short, diverse suggestions for a math assistant. "
            "Each suggestion should be a single sentence or question and fit in 100 characters. "
            "IMPORTANT: Any mathematical expression must be wrapped in $...$ LaTeX delimiters. "
            "For example write '$x^n$' not 'x^n', write '$3x - 7 = 2x + 5$' not '3x - 7 = 2x + 5'. "
            "Return ONLY a JSON array of strings."
        ).format(count=count)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            timeout=settings.llm_timeout,
        )

        content = response.choices[0].message.content or "[]"
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return [str(item).strip() for item in data if str(item).strip()]
        except json.JSONDecodeError:
            pass

        # Fallback: parse as lines if the model didn't return JSON
        suggestions = [
            line.strip(" -•\t")
            for line in content.splitlines()
            if line.strip()
        ]
        return suggestions[:count]

    async def generate_followups(self, content: str, count: int = 3) -> list[str]:
        """
        Ask the LLM for follow-up questions based on the assistant's last reply.
        """
        count = max(1, min(count, 6))
        prompt = (
            "Given the assistant reply below, generate {count} short follow-up questions "
            "a user might ask next. Each should be under 100 characters. "
            "IMPORTANT: Any mathematical expression must be wrapped in $...$ LaTeX delimiters. "
            "For example write '$x^n$' not 'x^n', write '$\\sin(x)$' not 'sin(x)'. "
            "Return ONLY a JSON array of strings.\n\n"
            "Assistant reply:\n{content}"
        ).format(count=count, content=content)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            timeout=settings.llm_timeout,
        )

        raw = response.choices[0].message.content or "[]"
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(item).strip() for item in data if str(item).strip()]
        except json.JSONDecodeError:
            pass

        lines = [line.strip(" -•\t") for line in raw.splitlines() if line.strip()]
        return lines[:count]


# Module-level singleton
chat_service = ChatService()
