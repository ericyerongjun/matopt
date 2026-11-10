# Build from the repository root:
#   docker build -f backend/Dockerfile .
# docker compose does this automatically.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    curl \
    libopenblas-dev \
    liblapack-dev \
    gfortran \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/constraints.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt -c constraints.txt

# Japanese speech readings: fetch OpenJTalk's dictionary while site-packages is writable.
RUN python -c "import pyopenjtalk; pyopenjtalk.g2p('テスト')"

COPY backend/ ./
COPY skills ./skills

# The code stays root-owned so agent code cannot rewrite the server; only data is writable.
RUN mkdir -p /app/data/workspaces /app/data/data_agent \
    && useradd -m matopt && chmod -R a+rX /app && chown -R matopt:matopt /app/data
USER matopt

ENV DATA_DIR=/app/data \
    HOST=0.0.0.0 \
    SANDBOX_MODE=external \
    SKILLS_DIRS=/app/skills/matopt,/app/skills/data,/app/skills/anthropic

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Agent code on loopback could otherwise forge X-Forwarded-For.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
