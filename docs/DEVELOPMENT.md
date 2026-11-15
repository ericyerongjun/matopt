# MatOpt development guide

Start the application with [Docker Compose](../README.md#run-with-docker).
Use the native workflow below when editing backend or frontend code and needing
hot reload. Native commands assume a macOS or Linux shell.

## Native prerequisites

- Node.js 22 or later with npm.
- `uv` (the setup script can obtain Python 3.12), or an available Python 3.11+
  interpreter. Python 3.12 matches the container and CI.
- A configured `backend/.env` with `DEEPSEEK_API_KEY`.
- On Linux, working bubblewrap support for native agent execution. macOS uses
  Seatbelt. Prefer Docker if the host cannot support the native sandbox.

Stop the Compose services before starting the native backend because both use
port 8000:

```sh
docker compose down
./scripts/start.sh
```

The launcher installs dependencies on the first run, starts the backend and
frontend, and opens http://localhost:5173. The preview runtime starts on 5174.
Press Ctrl-C to stop the native services. Native data defaults to `backend/data/`;
it does not automatically share Docker's named data volume.

For separate development terminals, install first:

```sh
./scripts/setup.sh
```

Backend terminal:

```sh
cd backend
.venv/bin/python main.py
```

Frontend terminal:

```sh
cd frontend
npm run dev
```

## Checks

From `frontend/`:

```sh
npm run typecheck
npm run lint
npm test
npm run build
```

From `backend/`:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q ../scripts/tests
```

From the repository root:

```sh
backend/.venv/bin/python scripts/generate_ui_types.py --check
bash scripts/vendor_check.sh
docker compose config --quiet
docker compose build
```

CI additionally runs lint and dependency audits. Backend installs use
`backend/constraints.txt`; frontend installs use `frontend/package-lock.json`.
Use meaningful tests for changed behavior and a production build for UI changes.

## Speech preflight

For an optional command-line check, run this from the host with Python 3.11+
while the Docker or native backend is running:

```sh
python3 scripts/demo_preflight.py --warmup --language en
```

The script uses only Python's standard library. It verifies configured
credentials and exercises local speech recognition/synthesis without making a
paid model request. Use `--language zh` for Mandarin or `--base-url` for another
backend. If API authentication is enabled, supply `API_AUTH_TOKEN` in the host
terminal environment. Do not execute this script inside the backend container:
the default external-sandbox policy rejects unauthenticated API calls from there.

This optional developer check requires host Python. The ordinary Docker setup
and browser voice check do not. Neither the preflight nor `/health` proves that
the provider accepts the API key or that browser microphone permissions work.

## Optional workflows

- [Model-quality evaluations](../evals/README.md) run real, paid model requests.
  Saved historical results describe their recorded configuration, not automatic
  validation of the current Ultrafast profile.
- `scripts/setup_lean.sh` installs the optional Lean toolchain, with Mathlib when
  requested. Configure `LEAN_COMMAND` and `LEAN_PROJECT_DIR` as needed. This host
  installation does not install Lean into the Compose backend.
- For OpenTelemetry in native development, install
  `backend/requirements-otel.txt` and set `OTEL_EXPORTER_OTLP_ENDPOINT`.
  Container deployments need those dependencies added to their image.
- [Academic guidance](../backend/app/academic_guidance.md) is shared by the
  assistant, answer writer, checker and data agent. Restart the native backend
  after editing it, or rebuild when using Docker.
- [Skills](../skills/README.md), [memory](MEMORY.md),
  [interactive UI](INTELLIGENT-UI.md) and [the API protocol](PROTOCOL.md) describe
  their respective extension points. ZCode source mapping is documented in
  [the UI migration notes](ZCODE-ADE-MIGRATION.md).

## Docker development notes

Compose is the complete application setup: it uses `backend/Dockerfile`,
`frontend/Dockerfile` and `frontend/sandbox/Dockerfile`. The root `Dockerfile`
is a separate single-container alternative and does not start the isolated
preview service. Use Compose for the complete application and demo.

Application source is copied into the images, so rebuild after code changes.
The repository's `skills/` directory is mounted read-only; restart the backend
after changing the skill catalog. Host executables and host-only paths are not
automatically available to container tools.

When changing the preview origin, Compose's `UI_SANDBOX_ORIGIN` must reach both
the frontend build argument and its runtime environment so the iframe URL and
content policy agree. See [Interactive UI deployment](INTELLIGENT-UI.md).
