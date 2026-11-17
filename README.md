# MatOpt

MatOpt is a learning and analysis assistant for mathematics, statistics, machine
learning and computer science. It combines chat with persistent Python
computation, document reading, interactive visualizations and a speaking 3D tutor.

**Use Docker Compose to run MatOpt.** It builds and starts the backend,
web interface and isolated visualization runtime together. No host Python or
Node.js installation is required.

## Run with Docker

Run all commands below from the repository root, beside `docker-compose.yml`.

### 1. Prepare Docker and the API key

Start Docker Desktop, or Docker Engine with the Compose plugin on Linux. Use
Linux containers. Check that the daemon and Compose are available:

```sh
docker info
docker compose version
```

For the first setup, create the backend configuration. If `backend/.env` already
exists, keep it and edit that file instead of copying over it.

```sh
cp backend/.env.example backend/.env
```

Open `backend/.env` and fill in `DEEPSEEK_API_KEY`. Keep the key private. The
remaining defaults are sufficient for the local demo; Compose sets
the container paths, listening address and execution mode automatically.

### 2. Build and start

```sh
docker compose up --build -d
docker compose ps
```

Open **[http://localhost:3000](http://localhost:3000)**. The first build downloads
dependencies, and the first backend start downloads speech models in the
background. An internet connection is also required for model requests and
online research. Wait for `backend` to become healthy; speech may still be warming.

| Service | Local address | Purpose |
| --- | --- | --- |
| `frontend` | http://localhost:3000 | Main application; proxies API requests to the backend |
| `backend` | http://localhost:8000 | FastAPI, agent execution and persistent Python kernels |
| `ui-sandbox` | http://localhost:3001 | Isolated HTML and JavaScript previews; opened by the app |

All three ports are published on `127.0.0.1`. Use the main application on port
3000 for the demonstration. The preview service on 3001 is not a second chat UI.

### 3. Verify before presenting

Check the API and the frontend proxy:

```sh
docker compose exec -T backend curl -fsS http://127.0.0.1:8000/health
```

Also open [http://localhost:3000/health](http://localhost:3000/health) in the
browser. A successful health check confirms the server is reachable; it does
not validate the API key against the provider or test the microphone.

In the main app, allow microphone access, open the Tutor panel and test one
spoken question and its audio reply. This exercises the browser and model as
well as speech. Keep **Ultrafast Demo** enabled. Speech download/loading status
is available at [the speech status endpoint](http://localhost:3000/api/speech/status).
Follow [the demo guide](docs/DEMO.md) for prompts and rehearsal steps; an optional
command-line speech check is in [the development guide](docs/DEVELOPMENT.md#speech-preflight).

## Everyday commands and saved data

| Action | Command |
| --- | --- |
| Start again using the existing images | `docker compose up -d` |
| Inspect services | `docker compose ps` |
| Follow backend logs | `docker compose logs -f --tail=100 backend` |
| Rebuild after source or dependency changes | `docker compose up --build -d` |
| Apply changes to `backend/.env` | `docker compose up -d --force-recreate` |
| Stop and remove containers, keeping saved data | `docker compose down` |

Chats, uploaded files, workspaces and speech models live under `/app/data` in
the named `backend-data` volume. Rebuilding or recreating containers keeps that
volume, but running Python kernels restart and lose memory-only variables.
`docker compose down -v` deletes the volume and its saved data; do not use it for
an ordinary shutdown. Configuration stays in the host's `backend/.env`.

## Troubleshooting

| Symptom | Check or action |
| --- | --- |
| Cannot connect to the Docker daemon | Start Docker Desktop and wait for its engine to be ready; check `docker info`. |
| `backend/.env` is missing | Complete the first setup step and enter the API key. |
| Port 3000, 3001 or 8000 is already allocated | Stop the earlier instance or the program using that port. Do not run native and Docker backends together on port 8000. |
| Backend is unhealthy or the frontend reports 502 | Read `docker compose logs --tail=100 backend frontend`; resolve the reported startup failure, then run `docker compose up -d`. |
| API key changes are not taking effect | Recreate the services with `docker compose up -d --force-recreate`; a plain restart does not reload Compose's environment file. |
| Speech is slow or unavailable | Allow the first model downloads to finish, check backend logs and retry the voice interaction. Use localhost and grant browser microphone permission. |
| Chat works but an HTML preview fails | Check `docker compose ps ui-sandbox` and its logs. Keep port 3001 available; arbitrary external libraries and network requests are restricted. |

## Implemented features

### Basic features

- Domain-specific dialogue: explain concepts, solve typical mathematical and
  statistical questions, discuss algorithms, and respond to follow-up questions.
- English and multilingual responses, formatted with readable Markdown and LaTeX.
- Essential analytical tasks: use Python to compute, inspect uploaded data, fit
  models and produce charts, retaining variables within a conversation.

### Additional features

| Feature | Implementation and capability | Limits or prerequisites |
| --- | --- | --- |
| Voice interaction | Browser recording, local faster-whisper transcription, Kokoro synthesis, read-aloud and interruptible hands-free conversation. | Microphone permission, downloaded speech models and adequate CPU; mathematical speech can be imperfect. |
| Animated 3D tutor | Three.js and VRM render a tutor with mouth motion, blinking and speaking/listening/working states; custom VRM import. | Approximate lip sync, WebGL support and avatar asset licenses. |
| Interactive explanations | Validated native controls, equations, tables, charts and diagrams; isolated HTML mini-apps; shared inline/canvas state and registered Python recomputation. | Generated apps can fail; controls only recompute through declared actions; the separate mini-app runtime must run. |
| PDF and image understanding | Page-oriented vision processing preserves a Markdown extraction, original files and page-coverage reporting. | API access, OCR mistakes and extra per-page time; unreadable pages must be acknowledged. |
| Persistent computational workspace | A per-conversation Python kernel, files, shell tools, notebook capture and previews. | Kernel restarts lose in-memory variables; code execution remains subject to sandbox and tool policy. |
| Bounded agent execution | Operation-specific repeated-failure checks, shared request/tool/time budgets, cancellation and limited optional subagents. | Stops bound work but do not prove correctness or guarantee quick completion. |
| Result verification | Optional Check results runs at most two checks and one full correction; provisional results stay labelled. Optional Lean proof checking. | LLM checking can miss errors; Lean/Mathlib must be installed and only submitted formal proofs are checked. |
| Durable conversations | SQLite-backed runs and events, reconnect, stop, pause/resume, mid-run steering, conversation management and exports. | Persistence does not preserve arbitrary kernel state across a process restart. |
| Session and personal memory | Rolling compaction, recall and structured session notes; optional cross-chat retrieval and learned memory. | Personal memory is opt-in; retrieval can miss relevant facts and is not a complete course knowledge base. |
| Skills and MCP | Slash-command skills, tool discovery, external MCP integrations, local plugin/config import and MCP export. | Connectors require their own services/credentials; bundled workflows are not all demonstrated integrations. |
| Mat and Opt task modes | Mat focuses on explanation/computation; Opt permits workspace editing; Auto routes each turn. | Routing is heuristic and may need an explicit mode selection. |
| Execution controls | Container boundary, execution rules, filtered child environments and optional human/guardian review. Native development also supports OS sandboxing. | Compose conversations share one backend container; they are not separately isolated containers. |
| Automation and inspection | Scheduled/repeated prompts, background jobs, file changes, activity summaries and opt-in detailed diagnostics. | Requires the backend to remain running; optional features need configuration. |
| Ultrafast Demo | Enabled by default in a fresh browser; real non-thinking inference, concise output, heuristic titles and safe reuse of completed answers. | No latency guarantee. The only toggle is Ultrafast Demo; off uses the configured model default. Check results still works. |

**Model integration:** MatOpt calls a pretrained DeepSeek API model. It does
not train or fine-tune an LLM. Skills, prompts, tools and memory supply the specialization.

## Using the interface

- **Compute and explain:** attach data, ask a question, then ask a follow-up in
  the same conversation to reuse its Python variables. Use **Mat** for analysis,
  **Opt** for workspace changes or **Auto** for per-turn routing.
- **Speak and listen:** click the microphone or hold Right Option on macOS
  (Right Alt elsewhere). Use **Read aloud** or enable **Speak** in the Tutor
  panel. Hands-free **Talk** mode sends speech and plays replies; speaking again
  interrupts playback. The language picker coordinates replies and speech.
- **Explore results:** use **Open in canvas** to expand an interactive result,
  **Files** for saved artifacts and **Activity** for progress and usage.
- **Control a run:** stop, pause/resume or send a follow-up while work is running.
  Enable **Check results** when a bounded verification pass is useful.
- **Choose response behavior:** **Ultrafast Demo** is enabled by default and is
  the only response-profile toggle. Turning it off uses the configured model
  default. There is no manual reasoning-effort menu.
- **Extend a task:** type `/` to browse skills and tools. Optional memory,
  connectors and other capabilities are managed in Settings.

The avatar is a fan-made tribute, not an endorsement by Andrej Karpathy. Custom
VRM models can be loaded in the Tutor panel; see the asset credits in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Documentation

| Guide | Contents |
| --- | --- |
| [Demo guide](docs/DEMO.md) | Docker warmup, sample data, prompts and timing |
| [Rehearsal report](docs/DEMO-VERIFICATION.md) | Real model tests, measured durations, failures and checked fallbacks |
| [Backend reference](backend/README.md) | Configuration, document processing, budgets, persistence and MCP |
| [Development guide](docs/DEVELOPMENT.md) | Optional native setup, tests, evaluation and debugging |
| [API protocol](docs/PROTOCOL.md) | Requests, streaming events and inspection routes |
| [Interactive UI](docs/INTELLIGENT-UI.md) | Native surfaces, mini-app isolation and actions |
| [Skills catalog](skills/README.md) | Bundled playbooks and skill discovery |
| [Memory](docs/MEMORY.md) | Session recall and optional personal memory |
| [Codex integration](docs/CODEX-INTEGRATION.md) | Adapted agent mechanisms and contribution boundaries |

## License and authorship

MatOpt's original application author is **Rongjun (Eric) Ye**. This distribution
licenses the original material he owns for **COMP4431 assessment only**, under
[LICENSE](LICENSE). Assessment, demonstration, marking, moderation and required
record retention are permitted. Commercial use, public redistribution, general
teaching reuse and use in another research or software project require separate
written permission. Attribution alone is not permission; receiving or grading
this submission does not transfer ownership. Contact: yerongjun03@gmail.com.
MatOpt remains under development.

Third-party components and other contributors' work retain their own terms;
see [NOTICE](NOTICE), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[the license directory](LICENSES/README.md). Their license texts do not grant
permission to reuse all of MatOpt. Valid rights independently granted under an
earlier license remain intact. The current academic terms are source-available,
not an open-source license. Keep author credits and do not claim another person's
work as your own. [CITATION.cff](CITATION.cff) supplies citation metadata, not
additional reuse permission.
