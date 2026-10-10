from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import followups as api
from app.llm.base import CompletionResult


class SideTaskRecorder:
    def __init__(self):
        self.events = []

    async def record_side_task(self, conversation_id, data):
        self.events.append((conversation_id, data))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [(ValueError("invalid completion"), 400), (RuntimeError("connection failed"), 502), (None, None)])
async def test_followup_attempt_has_terminal_record_and_matching_identity(monkeypatch, failure, status):
    recorder = SideTaskRecorder()
    rt = SimpleNamespace(harness=recorder, db=SimpleNamespace(get_conversation=lambda cid: {"id": cid}))

    class Provider:
        async def complete(self, messages, options):
            if failure is not None:
                raise failure
            return CompletionResult('["Check another row?"]', {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})

    registry = SimpleNamespace(resolve=lambda *_: (SimpleNamespace(id="fake"), SimpleNamespace(id="model")),
                               get=lambda *_: Provider())
    monkeypatch.setattr(api, "get_runtime", lambda: rt)
    monkeypatch.setattr(api, "providers", registry)
    body = api.FollowUpRequest(content="The slope is two.", conversation_id="c_example")
    if status is not None:
        with pytest.raises(HTTPException) as error:
            await api.followups(body)
        assert error.value.status_code == status
    else:
        result = await api.followups(body)
        assert result["followups"] == ["Check another row?"]
        assert result["usage"]["total_tokens"] == 5
    assert len(recorder.events) == 2
    start, terminal = [data for _, data in recorder.events]
    assert start["status"] == "started"
    assert terminal["status"] == ("error" if failure else "completed")
    assert start["task_id"] == terminal["task_id"]
    if failure:
        assert terminal["usage"] is None
    else:
        assert terminal["usage"]["total_tokens"] == 5
