import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.tools.base import ToolContext
from app.main import app


@pytest.mark.asyncio
async def test_lambda_api_v1_health_and_config():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "lambda-api"
        assert body["status"] == "healthy"
        assert body["code_execution"] == "local"

        r = await client.get("/api/v1/public/config")
        assert r.status_code == 200
        assert r.json()["local_mode"] is True

        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["lambda"] is True
        assert "lambda_data_agent" in r.json()["tools"]

        r = await client.get("/api/v1/conversations")
        assert r.status_code == 200
        assert r.json() == []


def test_lambda_original_agent_module_is_present():
    from lambda_app.services import agent_service, local_code_execution
    from lambda_app.api.v1 import chat

    assert hasattr(agent_service.AgentService, "execute_tool")
    assert hasattr(local_code_execution.LocalCodeExecutionService, "execute")
    assert hasattr(chat, "stream_chat_response")
    assert "AUTONOMOUS EXPLORATION" in chat.AUTONOMOUS_EXPLORATION_PROMPT


@pytest.mark.asyncio
async def test_lambda_tool_requires_message_or_autonomous(tmp_path):
    import asyncio

    from app.agent.events import Event
    from app.agent.tools.lambda_tool import LambdaDataAgentTool
    from app.agent.workspace import Workspace

    ws = Workspace("c", tmp_path / "ws")
    events = []

    async def emit(e: Event):
        events.append(e)

    ctx = ToolContext(conversation_id="c", workspace=ws, emit=emit, cancel=asyncio.Event())
    r = await LambdaDataAgentTool()(ctx, {})
    assert not r.ok
    assert "message" in (r.error or "").lower() or "autonomous" in (r.error or "").lower()
