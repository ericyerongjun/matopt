# ── Stage 1: Build the frontend ──────────────────────────────────────────
FROM node:22-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --frozen-lockfile 2>/dev/null || npm install
COPY frontend/ .
RUN npm run build

# ── Stage 2: Python backend ─────────────────────────────────────────────
FROM python:3.10-slim AS backend

# System deps: pandoc (for export), poppler (for MinerU PDF), build tools,
#              BLAS/LAPACK (numpy/scipy), gfortran (numba/scipy wheel build)
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

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install Python deps (cached layer)
COPY backend/pyproject.toml backend/requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

# Copy backend source
COPY backend/ .

# Copy built frontend into a static directory the backend can serve
COPY --from=frontend-build /app/frontend/dist /app/static

# Create data directories
RUN mkdir -p /app/uploads /app/exports

# Non-root user
RUN useradd -m matopt && chown -R matopt:matopt /app
USER matopt

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
