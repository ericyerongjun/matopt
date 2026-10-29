import openai
import json
from contextlib import aclosing
from typing import List, Dict, Any, AsyncGenerator
from data_agent_app.core.config import settings
from app.llm.catalog import MAX_OUTPUT_TOKENS, MODEL_ID, PROVIDER_ID, canonical_model
from app.agent.budget import BudgetExhausted
from app.agent.data_agent_bridge import controlled_items, model_attempt


class LLMService:
    def __init__(self):
        self.client = openai.AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY or "not-configured",
            base_url=settings.DEEPSEEK_BASE_URL,
            max_retries=0,
        )
        self.default_model = MODEL_ID

    def _normalize_model(self, model: str) -> str:
        return canonical_model(model)

    def _client_for_model(self, model: str):
        normalized_model = self._normalize_model(model)
        return self.client, normalized_model

    def _get_delta_extra_text(self, delta: Any, *field_names: str) -> str:
        """Read provider-specific streaming fields from OpenAI SDK objects."""
        for field_name in field_names:
            value = getattr(delta, field_name, None)
            if value:
                return value
        model_extra = getattr(delta, "model_extra", None) or {}
        for field_name in field_names:
            value = model_extra.get(field_name)
            if value:
                return value
        return ""

    def _usage_to_dict(self, usage: Any) -> Dict[str, Any] | None:
        """Normalize OpenAI-compatible token usage objects."""
        if not usage:
            return None

        def read(obj: Any, key: str, default: int = 0) -> int:
            if obj is None:
                return default
            if isinstance(obj, dict):
                return int(obj.get(key) or default)
            return int(getattr(obj, key, default) or default)

        details = getattr(usage, "completion_tokens_details", None)
        if isinstance(usage, dict):
            details = usage.get("completion_tokens_details") or details

        raw = usage
        if hasattr(usage, "model_dump"):
            raw = usage.model_dump()
        elif not isinstance(usage, dict):
            raw = {
                key: getattr(usage, key)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if hasattr(usage, key)
            }

        prompt_tokens = read(usage, "prompt_tokens")
        completion_tokens = read(usage, "completion_tokens")
        total_tokens = read(usage, "total_tokens", prompt_tokens + completion_tokens)
        reasoning_tokens = read(details, "reasoning_tokens")

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
            "raw": raw,
        }

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream chat completion with tool support.
        Uses model default temperature and max_tokens."""
        try:
            model = self._normalize_model(model or self.default_model)
            client, provider_model = self._client_for_model(model)

            # Define tools for the model - MUST include session_id for persistence
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "execute_python",
                        "description": f"Execute Python code in sandbox. Variables persist within the same conversation session. Working directory: /tmp/workspace/",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "code": {
                                    "type": "string",
                                    "description": "Python code to execute. Use plt.savefig() to save charts. Variables persist between calls."
                                },
                                "session_id": {
                                    "type": "string",
                                    "description": "Session ID for variable persistence (use conversation_id)"
                                }
                            },
                            "required": ["code", "session_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "execute_shell",
                        "description": (
                            "Execute shell commands in sandbox. Working directory: /tmp/workspace/. "
                            "First use preinstalled Python packages. If installation is essential, use a single "
                            "simple command like `/opt/python/versions/cpython-3.11.14-linux-x86_64-gnu/bin/python3 -m pip install package-name --break-system-packages --no-cache-dir`; do not use apt-get, sudo, "
                            "pip3, shell chaining, pipes, or redirection. "
                            "LaTeX is available via pdflatex. Example: "
                            "pdflatex -interaction=nonstopmode -halt-on-error "
                            "-output-directory /tmp/workspace /tmp/workspace/report.tex. "
                            "For Chinese PDF reports, use xelatex with xeCJK/ctex and Noto CJK fonts. Do not use tlmgr at runtime. "
                            "For long LaTeX/PDF reports, read /tmp/workspace/.skills/latex_skill.md first. "
                            "For PPT or slide deck requests, read /tmp/workspace/.skills/ppt_skill.md first; "
                            "prefer LaTeX Beamer PDF slides unless the user explicitly asks for editable .pptx."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": "Shell command to execute (ls, cat, python3, pdflatex, xelatex, etc.)"
                                },
                                "session_id": {
                                    "type": "string",
                                    "description": "Session ID for sandbox isolation"
                                }
                            },
                            "required": ["command", "session_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": f"Read file contents from sandbox. Use full path like /tmp/workspace/filename",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Full path to the file in sandbox"
                                },
                                "session_id": {
                                    "type": "string",
                                    "description": "Session ID for sandbox isolation"
                                }
                            },
                            "required": ["path", "session_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "description": f"Write text file to sandbox. Supports csv, json, txt, html, etc. Working directory: /tmp/workspace/",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "File path (relative to /tmp/workspace/ or full path)"
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Content to write to the file"
                                },
                                "session_id": {
                                    "type": "string",
                                    "description": "Session ID for sandbox isolation"
                                }
                            },
                            "required": ["path", "content", "session_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "edit_file",
                        "description": f"Edit/replace content in an existing file in sandbox. Uses string replacement.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Full path to the file in sandbox"
                                },
                                "old_string": {
                                    "type": "string",
                                    "description": "String to find and replace"
                                },
                                "new_string": {
                                    "type": "string",
                                    "description": "Replacement string"
                                },
                                "session_id": {
                                    "type": "string",
                                    "description": "Session ID for sandbox isolation"
                                }
                            },
                            "required": ["path", "old_string", "new_string", "session_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "glob_files",
                        "description": "Find files in sandbox by glob pattern. Use before reading unknown file names.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "pattern": {
                                    "type": "string",
                                    "description": "Glob pattern, e.g. *.csv, **/*.png, report*.md"
                                },
                                "path": {
                                    "type": "string",
                                    "description": "Directory or file to search. Defaults to /tmp/workspace/"
                                },
                                "max_results": {
                                    "type": "integer",
                                    "description": "Maximum number of matches to return"
                                },
                                "session_id": {
                                    "type": "string",
                                    "description": "Session ID for sandbox isolation"
                                }
                            },
                            "required": ["pattern", "session_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "grep_files",
                        "description": "Search file contents in sandbox. Use for code, logs, reports, and generated text files.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "pattern": {
                                    "type": "string",
                                    "description": "Regex or literal text to search for"
                                },
                                "path": {
                                    "type": "string",
                                    "description": "File or directory to search. Defaults to /tmp/workspace/"
                                },
                                "include_glob": {
                                    "type": "string",
                                    "description": "Only search matching file names, e.g. *.py or *.md"
                                },
                                "case_sensitive": {
                                    "type": "boolean",
                                    "description": "Whether matching is case-sensitive"
                                },
                                "max_results": {
                                    "type": "integer",
                                    "description": "Maximum number of matches to return"
                                },
                                "session_id": {
                                    "type": "string",
                                    "description": "Session ID for sandbox isolation"
                                }
                            },
                            "required": ["pattern", "session_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "update_todo",
                        "description": "Create or update a todo list for long multi-step analysis. Use only when the task likely needs 5+ meaningful steps; otherwise proceed directly.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "todos": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "content": {
                                                "type": "string",
                                                "description": "Task description"
                                            },
                                            "status": {
                                                "type": "string",
                                                "enum": ["pending", "in_progress", "completed"],
                                                "description": "Task status"
                                            }
                                        },
                                        "required": ["content", "status"]
                                    }
                                },
                                "session_id": {
                                    "type": "string",
                                    "description": "Session ID for todo isolation"
                                }
                            },
                            "required": ["todos", "session_id"]
                        }
                    }
                }
            ]

            create_kwargs = {
                "model": provider_model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "stream": True,
                "reasoning_effort": "high",
                "extra_body": {"thinking": {"type": "enabled"}},
            }

            async with aclosing(self._stream_completion(client, create_kwargs)) as completion:
                async for item in completion:
                    yield item

        except BudgetExhausted:
            raise
        except Exception as e:
            yield {
                "type": "error",
                "content": str(e)
            }

    async def _stream_completion(self, client, create_kwargs):
        async with model_attempt(provider=PROVIDER_ID, model=create_kwargs["model"]) as attempt:
            stream = None
            try:
                stream = await attempt.wait(lambda: client.chat.completions.create(
                    **create_kwargs, stream_options={"include_usage": True},
                ))
                tool_calls = []
                content = ""
                reasoning_content = ""
                usage_summary = None
                incomplete_reason = None

                async for chunk in controlled_items(stream, attempt.context):
                    attempt.observe_usage(getattr(chunk, "usage", None))
                    chunk_usage = self._usage_to_dict(getattr(chunk, "usage", None))
                    if chunk_usage:
                        usage_summary = chunk_usage

                    # Skip if no choices
                    if not chunk.choices:
                        continue

                    finish_reason = getattr(chunk.choices[0], "finish_reason", None)
                    if finish_reason == "length":
                        # Keep the text already produced, but never execute calls
                        # from an unfinished response. The usage receipt may be
                        # in a later, choices-empty chunk, so drain it first.
                        incomplete_reason = finish_reason
                    if finish_reason in {"error", "aborted", "insufficient_system_resource"}:
                        attempt.outcome = "error"
                        attempt.provider_finished = True
                        yield {"type": "error", "content": f"DeepSeek ended the stream with {finish_reason}",
                               "usage": usage_summary}
                        return

                    delta = chunk.choices[0].delta

                    # Handle thinking/reasoning content. Some providers require the
                    # accumulated thinking to be passed back with tool-call history.
                    reasoning_delta = self._get_delta_extra_text(delta, "reasoning_content", "thinking") if delta else ""
                    if reasoning_delta:
                        reasoning_content += reasoning_delta
                        yield {
                            "type": "reasoning",
                            "content": reasoning_delta
                        }

                    # Handle content
                    if delta and delta.content:
                        content += delta.content
                        yield {
                            "type": "delta",
                            "content": delta.content
                        }

                    # Handle tool calls
                    if delta and delta.tool_calls:
                        for tc in delta.tool_calls:
                            index = tc.index if tc.index is not None else 0
                            while index >= len(tool_calls):
                                tool_calls.append({
                                    "id": "",
                                    "type": "function",
                                    "function": {
                                        "name": "",
                                        "arguments": ""
                                    }
                                })

                            if tc.id:
                                tool_calls[index]["id"] = tc.id
                            if tc.function.name:
                                tool_calls[index]["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls[index]["function"]["arguments"] += tc.function.arguments

                attempt.provider_finished = True
                if incomplete_reason is not None:
                    attempt.outcome = "error"
                    yield {
                        "type": "error",
                        "content": "The delegated model reached its output limit before completing the response.",
                        "incomplete": True,
                        "finish_reason": incomplete_reason,
                        "usage": usage_summary,
                    }
                    return
                for idx, tool_call in enumerate(tool_calls):
                    if not tool_call["id"]:
                        tool_call["id"] = f"tool_{idx}"
                    if not tool_call["function"]["name"]:
                        continue
                    try:
                        json.loads(tool_call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError as exc:
                        attempt.outcome = "error"
                        yield {
                            "type": "error",
                            "content": f"Incomplete tool arguments for {tool_call['function']['name']}: {exc}"
                        }
                        return
                    yield {
                        "type": "tool_call",
                        "tool_call": tool_call
                    }

                yield {
                    "type": "done",
                    "content": content,
                    "tool_calls": tool_calls,
                    "thinking": reasoning_content,
                    "usage": usage_summary
                }
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    await close()

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096
    ) -> str:
        """Non-streaming chat completion."""
        try:
            model = self._normalize_model(model or self.default_model)
            client, provider_model = self._client_for_model(model)
            if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
                raise ValueError(f"max_tokens must be between 1 and {MAX_OUTPUT_TOKENS}.")

            async with model_attempt(provider=PROVIDER_ID, model=provider_model) as attempt:
                response = await attempt.wait(lambda: client.chat.completions.create(
                    model=provider_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort="none",
                    extra_body={"thinking": {"type": "disabled"}},
                ))
                attempt.provider_finished = True
                attempt.observe_usage(getattr(response, "usage", None))
                return response.choices[0].message.content
        except BudgetExhausted:
            raise
        except Exception as e:
            return f"Error: {str(e)}"

    def generate_title(self, first_message: str) -> str:
        """Generate a title for a conversation based on the first message."""
        try:
            import openai as sync_openai
            client = sync_openai.OpenAI(
                api_key=settings.DEEPSEEK_API_KEY or "not-configured",
                base_url=settings.DEEPSEEK_BASE_URL,
                max_retries=0,
            )

            response = client.chat.completions.create(
                model=MODEL_ID,
                messages=[
                    {
                        "role": "system",
                        "content": "Generate a short, concise title (max 5 words) for a conversation based on the user's first message. Return only the title, no quotes."
                    },
                    {
                        "role": "user",
                        "content": first_message
                    }
                ],
                temperature=0.3,
                max_tokens=20,
                reasoning_effort="none",
                extra_body={"thinking": {"type": "disabled"}},
            )

            return response.choices[0].message.content.strip() or "New Conversation"
        except Exception:
            # Fallback to truncated message
            return first_message[:50] + "..." if len(first_message) > 50 else first_message
