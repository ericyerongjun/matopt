from fastapi import APIRouter
from data_agent_app.api.v1 import files

# Only the links in generated output are served. The data agent itself runs
# through the harness tool, which calls stream_chat_response directly.
api_router = APIRouter()

api_router.include_router(files.router, prefix="/files", tags=["files"])
