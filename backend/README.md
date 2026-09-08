# MatOpt backend

The agentic FastAPI service: a tool-calling harness with a persistent Python kernel,
workspace files, Agent Skills routing, an MCP sandbox, optional MinerU document parsing,
and streaming SSE events.

## Environment

```bash
cp backend/.env.example backend/.env
```

Add at least one of `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, or `QWEN_API_KEY`.
The header picker exposes DeepSeek V4, GPT-5.6, and Qwen 3.x, each with a
reasoning-effort slider (`off` … `max`).

## Local run (no uv)

```bash
# from repo root
./scripts/setup.sh
cd backend && .venv/bin/python main.py
```

Python comes from Homebrew (`python@3.12`). Dependencies are plain pip.

- API: http://localhost:8000
- Health: http://localhost:8000/health
- Providers: http://localhost:8000/api/providers
- Protocol: [docs/PROTOCOL.md](../docs/PROTOCOL.md)

Optional PDF OCR:

```bash
./scripts/setup_mineru.sh
```

## Docker Compose

From the repository root:

```bash
docker compose up --build
```

Frontend: http://localhost:3000 · backend: http://localhost:8000
