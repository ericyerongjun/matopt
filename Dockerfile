# Single-container alternative: docker build -t matopt .
# docker compose builds the separate backend and frontend Dockerfiles.
# ── Stage 1: Build the frontend ──────────────────────────────────────────
FROM node:22-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# ── Stage 2: Python backend ─────────────────────────────────────────────
FROM python:3.12-slim AS backend

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

COPY backend/ .
COPY --from=frontend-build /app/frontend/dist /app/static

# The code stays root-owned so agent code cannot rewrite the server; only data is writable.
RUN mkdir -p /app/data/workspaces \
    && useradd -m matopt && chmod -R a+rX /app && chown -R matopt:matopt /app/data
USER matopt

# Start with -e API_AUTH_TOKEN=<16+ characters>, or with
# -e ALLOW_UNAUTHENTICATED_BIND=true -p 127.0.0.1:8000:8000 for this machine only.
ENV DATA_DIR=/app/data \
    HOST=0.0.0.0 \
    SANDBOX_MODE=external

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Agent code on loopback could otherwise forge X-Forwarded-For.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
