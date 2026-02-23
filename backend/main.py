"""
Entry point for running the MatOpt backend.

    uv run python main.py
    # or
    uvicorn app.main:app --reload
"""

import uvicorn
from app.config import settings


def main():
    print(f"Starting {settings.app_name} backend on {settings.host}:{settings.port}")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
