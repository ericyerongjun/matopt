# LAMBDA backend (vendored)

Upstream: https://github.com/AMA-CMFAI/LAMBDA  
Commit: `83cc4651c2b4e736d110af6925e1b7b8fb254cef` (`main`)  
Paper: [LAMBDA: A Large Model Based Data Agent](https://www.tandfonline.com/doi/full/10.1080/01621459.2025.2510000) (JASA)  
Site: https://www.polyu.edu.hk/ama/cmfai/lambda.html

The original FastAPI package is named `app`, same as MatOpt, so it is vendored as `backend/lambda_app/` with a mechanical rewrite of `from app.` → `from lambda_app.`. Agent loop, tools, models, chat stream, export, and `lambda_app/skills/` are upstream code.

Refresh:

```bash
git clone --depth 1 https://github.com/AMA-CMFAI/LAMBDA /tmp/LAMBDA
python3 scripts/vendor_lambda_backend.py /tmp/LAMBDA
```

## What MatOpt adds (not upstream)

- Package name `lambda_app` so both backends can import in one process.
- Data root `DATA_DIR/lambda/` (sqlite, workspaces, uploads, images, latex) so LAMBDA does not share `backend/data/workspaces/` with MatOpt conversations.
- Shared `backend/.env`: unknown MatOpt keys are ignored; empty `OPENAI_API_KEY` inherits `DEEPSEEK_API_KEY`.
- OpenAI Python SDK 2.x (MatOpt) rejects an empty key at client init; `llm_service` passes `"not-configured"` when unset so the original module still imports.
- Host: `app.data.lambda_host.attach_lambda` mounts the original router at **`/api/v1`** (LAMBDA's own URL prefix, no clash with MatOpt `/api/...`).
- Tool `lambda_data_agent` plus split catalog skills under `skills/lambda/` (`lambda-data-agent`, `lambda-eda`, `lambda-report`, `lambda-slides`, `lambda-notebook`) so the harness can invoke LAMBDA's original `stream_chat_response` or a focused deliverable.

Standalone extras kept for reference: `backend/lambda/main.py`, `requirements.txt`, `env.example`.

## Licence

The upstream repository does not include a LICENSE file. Sources are copied as-is for use as MatOpt's data agent. Review AMA-CMFAI/LAMBDA before distributing.
