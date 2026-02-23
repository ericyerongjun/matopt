# ── Backend Dockerfile (standalone, no frontend) ────────────────────────
FROM python:3.10-slim

# System deps: pandoc (export), poppler (MinerU PDF), OpenGL (OCR/torch),
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

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install Python deps (cached layer — only re-runs when deps change)
COPY pyproject.toml requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

# Copy source
COPY . .

# Data directories
RUN mkdir -p /app/uploads /app/exports

# Non-root user
RUN useradd -m matopt && chown -R matopt:matopt /app
USER matopt

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
