"""Search must not hold up public progress or concurrent tool completions."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.agent.tools import web_search


@pytest.mark.asyncio
async def test_blocked_sync_search_leaves_event_loop_and_cancellation_responsive(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def engine(query, limit):
        entered.set()
        release.wait(3)
        return [{"title": "Source", "href": "https://example.test"}]

    monkeypatch.setattr(web_search, "_ddgs", engine)
    task = asyncio.create_task(web_search.search_web("current release", 3, SimpleNamespace()))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), 2)
        assert not task.done(), "The network search blocked progress until it completed"
        # Other coroutine work and explicit cancellation happen while the
        # synchronous engine is still waiting for its result.
        assert await asyncio.wait_for(asyncio.sleep(0, result="responsive"), .5) == "responsive"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
