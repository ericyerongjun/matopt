import asyncio

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

        r = await client.get("/api/v1/chat/models")
        assert r.status_code == 200
        assert r.json() == {"models": [{"id": "deepseek-flash", "name": "DeepSeek V4.1 Flash", "multimodal": True}],
                            "default": "deepseek-flash"}

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


def _lambda_db():
    from lambda_app.core.database import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_register_uploads_sets_conversation_id_and_dedupes(tmp_path):
    from lambda_app.models.models import UploadedFile, User

    from app.agent.tools.lambda_tool import _register_workspace_uploads

    db = _lambda_db()
    try:
        user = User(email="bridge@lambda.local", hashed_password="x", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        (uploads / "data.csv").write_text("a,b\n1,2\n")
        dest = tmp_path / "lambda_uploads"
        first = _register_workspace_uploads(uploads, user.id, db, dest, conversation_id="99")
        second = _register_workspace_uploads(uploads, user.id, db, dest, conversation_id="99")
        assert first == second and first
        rows = db.query(UploadedFile).filter(UploadedFile.user_id == user.id).all()
        assert len(rows) == 1
        assert rows[0].conversation_id == "99"
        assert rows[0].original_name == "data.csv"
        assert rows[0].filename == "matopt_data.csv"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_sync_finds_files_by_explicit_ids(tmp_path):
    from lambda_app.models.models import UploadedFile, User
    from lambda_app.services.sandbox_file_manager import SandboxFileManager

    from app.agent.lambda_bridge import LambdaExecContext, bind_lambda_exec_context
    from app.agent.workspace import Workspace

    db = _lambda_db()
    try:
        user = User(email="sync@lambda.local", hashed_password="x", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        src = tmp_path / "src.csv"
        src.write_text("x\n1\n")
        rec = UploadedFile(
            user_id=user.id,
            conversation_id=None,
            filename="matopt_src.csv",
            original_name="src.csv",
            file_path=str(src),
            file_size=src.stat().st_size,
            mime_type="text/csv",
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        ws = Workspace("c_sync", tmp_path / "ws")
        mgr = SandboxFileManager()
        with bind_lambda_exec_context(LambdaExecContext(
            conversation_id="c_sync",
            workspace_root=ws.root,
            sandbox_session_id="matopt-c_sync",
        )):
            synced = await mgr.sync_session_files(
                "missing-session",
                db,
                user.id,
                sandbox_session_id="matopt-c_sync",
                file_ids=[rec.id],
            )
        assert any(item["filename"] == "src.csv" for item in synced)
        assert (ws.root / "src.csv").exists()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_lambda_python_uses_shared_matopt_kernel(tmp_path):
    from app.agent.events import Event
    from app.agent.kernel import KernelPool
    from app.agent.lambda_bridge import LambdaExecContext, bind_lambda_exec_context
    from app.agent.tools.base import ToolContext
    from app.agent.tools.python_tool import PythonTool
    from app.agent.workspace import Workspace
    from lambda_app.services.agent_service import AgentService

    ws = Workspace("c_kernel", tmp_path / "ws")
    kernels = KernelPool(lambda cid: ws)
    session = kernels.get("c_kernel")
    events = []

    async def emit(e: Event):
        events.append(e)

    ctx = ToolContext(
        conversation_id="c_kernel", workspace=ws, emit=emit, cancel=asyncio.Event(),
        call_id="c", kernels=kernels,
    )
    svc = AgentService()
    try:
        with bind_lambda_exec_context(LambdaExecContext(
            conversation_id="c_kernel",
            workspace_root=ws.root,
            kernel=session,
            sandbox_session_id="matopt-c_kernel",
        )):
            result = await svc._execute_python({"code": "shared = 21\nprint(shared)"})
        assert result["success"] and "21" in (result.get("stdout") or "")
        tool = PythonTool()
        again = await tool(ctx, {"code": "print(shared * 2)"})
        assert again.ok and "42" in again.output
        assert (ws.root / "analysis.ipynb").exists()
    finally:
        await kernels.shutdown_all()
