"""Conversation and run IDs on every log line, including tool and background tasks.

Asyncio tasks copy the context when they are created, so binding at the top of
a run covers everything that run starts.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Optional

_conversation_id: ContextVar[Optional[str]] = ContextVar("log_conversation_id", default=None)
_run_id: ContextVar[Optional[str]] = ContextVar("log_run_id", default=None)
_installed = False


def bind_log_context(*, conversation_id: Optional[str] = None, run_id: Optional[str] = None) -> None:
    """Set the IDs for this task and the tasks it starts; None leaves one unchanged."""
    if conversation_id is not None:
        _conversation_id.set(conversation_id)
    if run_id is not None:
        _run_id.set(run_id)


def log_context_suffix() -> str:
    parts = [f"{name}={value}" for name, value in (("conversation", _conversation_id.get()), ("run", _run_id.get())) if value]
    return f" [{' '.join(parts)}]" if parts else ""


def install_log_context() -> None:
    """Append the bound IDs to each message, whatever handler or format is in use."""
    global _installed
    if _installed:
        return
    _installed = True
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        record.conversation_id = _conversation_id.get()
        record.run_id = _run_id.get()
        suffix = log_context_suffix()
        if suffix:
            try:
                record.msg, record.args = record.getMessage() + suffix, None
            except (TypeError, ValueError):
                pass  # the logging module reports a malformed call itself at emit time
        return record

    logging.setLogRecordFactory(factory)
