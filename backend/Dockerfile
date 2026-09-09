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

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY skills ./skills

RUN mkdir -p /app/data/workspaces /app/data/lambda \
    && useradd -m matopt && chown -R matopt:matopt /app
USER matopt

ENV DATA_DIR=/app/data \
    SKILLS_DIRS=/app/skills/matopt,/app/skills/lambda,/app/skills/anthropic

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
