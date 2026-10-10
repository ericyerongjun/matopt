# Session history provenance

Reference: DeepSeek Harness, MIT, commit
`639ed015397290b3745d163aafe02ffee4aa3f84`.

- `derive.py` translates the live-node replacement fold from
  `packages/core/session/src/surface.ts`. Endpoints name current surface
  nodes; replacement spans follow their positions, not numeric seq ranges.
- `repair.py` adapts `packages/core/session/src/repair.ts`'s pending-call
  recovery and append-only lifecycle closers. The interrupted-tool text is
  copied verbatim; synthetic IDs and timestamps follow the same algorithm.
- `stream.py` adapts `packages/llm/llm/src/assistant-stream.ts`'s adjacent
  delta packing (`time0`, member gaps, raw usage/finish records).
- Lossless JSON validation, source-reference validation and range encoding
  use upstream invariants, with Python-specific type checks.

This is a reduced Python adaptation, not a byte-for-byte vendoring of the
TypeScript session engine. Provider messages retain MatOpt wire dictionaries,
timestamps use epoch **seconds**, persistence uses atomic SQLite transactions
instead of JSONL, and `ui/message` materializes the existing messages table.
No inherited legacy usage is reported as a precise provider attempt.

The repository third-party notices carry the upstream license. Runtime
checkpoints await batched writes on a worker thread before model dispatch and
tool execution. Compact deltas are stored together when an attempt finishes;
if a process dies while streaming, its open step is conservatively interrupted.

The chat transport remains the existing presentation-event SSE stream. Its
queue is bounded at 256 items and disconnects cancel the producer even while
the provider is silent. `id:` appears only on presentation events whose
`seq` is already durably checkpointed; token and usage deltas have no id.
`GET /api/conversations/{id}/events` provides exclusive `from_seq` paging over
raw durable session envelopes. It does **not** replay those envelopes as old
chat deltas, and chat streaming does not implement automatic Last-Event-ID
reconnection. The request inspector reconstructs historical provider inputs
on demand via `GET /api/conversations/{id}/requests/{header_seq}`.
