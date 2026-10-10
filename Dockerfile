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

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=frontend-build /app/frontend/dist /app/static

RUN mkdir -p /app/data/workspaces \
    && useradd -m matopt && chown -R matopt:matopt /app
USER matopt

ENV DATA_DIR=/app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
