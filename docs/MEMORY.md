# Memory and retrieval

MatOpt keeps two memory layers. They are separate by design: one belongs to a
single chat, the other to the user.

| | Session memory | Personal memory |
|---|---|---|
| Scope | One conversation | All conversations of the user |
| Role | The model's history and working context | Who the user is: preferences, level, notation, ongoing projects |
| Updated | During the chat, by the agent | Chat to chat: after a chat goes idle |
| Model calls | None of its own | One extraction per finished chat, one consolidation per pass |
| Default | On (`SESSION_MEMORY_ENABLED`) | Off (`MEMORIES_ENABLED`, Settings → Features) |
| Code | `backend/app/agent/memory/session.py` | `backend/app/agent/memory/{extract,consolidate,service}.py` |

Both read through one hybrid retrieval index (`backend/app/agent/memory/index.py`).

## Personal memory: the Codex pipeline, kept

Personal memory is the port of Codex's `memories` (see
[CODEX-INTEGRATION.md](CODEX-INTEGRATION.md)). We kept its structure after comparing it
with Mem0, A-MEM, Zep/Graphiti, Letta and LangMem:

1. **Phase 1, per chat:** a conversation that has been idle for
   `MEMORY_MIN_IDLE_HOURS` becomes one record: raw notes, a summary and a slug.
   The chat's session notes go first in the transcript, so the agent's own
   summary of goals and results is passed on.
2. **Phase 2, consolidation:** the newest records and the user's notes are
   folded into two files. `memory_summary.md` is injected into every system
   prompt. `MEMORY.md` is the longer registry the agent reads on demand.

Why we kept it rather than switching to an atomic-fact store with
ADD/UPDATE/DELETE (Mem0):

- **It is already the pattern the research now recommends.** Extraction and
  consolidation run in the background, after the chat ("sleep-time" in Letta;
  offline consolidation in LightMem). Mem0's own 2026 algorithm dropped
  destructive UPDATE/DELETE for add-only extraction, because a model deleting
  facts that do not contradict is a known failure mode.
- **It is safe.** Chats that read web or MCP output are not learned from, so
  third-party text cannot plant memories. Secrets are redacted at every step.
- **Everything is plain files.** The user can read and edit them.
- **For one user, a rewrite that fits in context is enough.** Phase 2 is
  capped at 64 inputs, so rewriting both files each pass stays cheap.

What MatOpt adds to Codex:

- **Ranked search.** `memory_search` used to match lines with grep. It now ranks
  sections of the memory files, notes and records with the hybrid index, and
  falls back to grep only for substrings BM25 cannot see.
- **Per-chat opt-out is enforced.** A chat with memory off gets no memory
  summary in its prompt, and the memory tools refuse in it. Before, the toggle
  only stopped extraction.
- **Deleting a chat forgets it.** Its record is removed, and the next
  consolidation drops what it taught. Its recall index and session notes go
  too. ChatGPT keeps memories from a deleted chat; we do not.
- **Past-chat search.** `recall` with `scope="past_chats"` searches other
  conversations on demand, like Claude's "search past chats", rather than
  injecting them always-on. Opted-out chats are never searched, and an
  opted-out chat cannot search the others.

## Session memory

Long chats are already compacted: the oldest turns become a
`<conversation-summary>` (`backend/app/agent/compaction.py`), so the prompt
stays within budget and the provider's prefix cache keeps working. Session
memory adds two things on top.

- **`recall`** (MemGPT recall memory, ReadAgent page lookup). It searches the
  chat's turns, including those folded into the summary, and returns turn
  numbers with snippets. `recall(turn=N)` reads a turn back verbatim, so a
  summary does not have to carry every number.
- **`session_notes`** (a Letta core block, Manus' `todo.md`, Anthropic's
  structured notes). A short Markdown block the agent edits: goal, notation,
  assumptions, established results (verified or conjectured), decisions and
  open questions.
  - It lives outside the history, so it survives compaction.
  - It is shown under `# Session notes` in the per-turn `<turn-context>`,
    only when it changed since the agent last saw it: after a user edit
    through the API, or after a compaction removed the earlier copy.
  - The system prompt therefore stays byte-stable, and unchanged notes do
    not repeat every turn.

Neither needs a background model call. Routes:
`GET|PUT|DELETE /api/conversations/{id}/session-memory`.

## Retrieval

The index is one SQLite file, `<data_dir>/recall.sqlite3`. It is a cache:
each source (the memory folder, or one conversation) is rebuilt when its
signature changes. Indexing makes no model calls.

Up to three rankings are fused with reciprocal rank fusion (k = 60):

1. **BM25 (SQLite FTS5)** over a LaTeX-aware form of the text: `\nabla f`
   is indexed as `nabla f`. Exact notation, identifiers and named results are
   where lexical search is strongest.
2. **Dense, optional.** Local embeddings via `model2vec`
   (`minishlab/potion-retrieval-32M`) or `fastembed`
   (`BAAI/bge-small-en-v1.5`), scored with numpy. DeepSeek has no embedding
   endpoint, so this is off by default (`MEMORY_EMBEDDING_BACKEND`).
   Vectors are computed lazily and redone when the model changes.
3. **Term graph** (`MEMORY_GRAPH_ENABLED`), after LinearRAG and HippoRAG 2.
   - Chunks and the terms they mention form a bipartite graph with no
     relation extraction.
   - Personalized PageRank (damping 0.85) is seeded from the query's terms
     and the first-stage hits. It finds chunks that share a named result or
     file with the hits without sharing their words: for "the rows of the
     data we fit the Lasso on", the turn that described `housing.csv`.
   - Terms that appear in many chunks (hubs) are dropped.
   - LinearRAG uses spaCy NER, which finds little in mathematics.
     `terms.py` extracts named results ("Hoeffding's inequality"),
     acronyms and CamelCase names (SGD, AdamW), bold defined terms,
     backticked identifiers and files, theorem labels and distinctive
     LaTeX operators.
   - Written from the paper; the LinearRAG code is GPL-3.0 and is not used.

We deliberately left out GraphRAG, LightRAG and HippoRAG's OpenIE: they
spend one or more LLM calls per chunk to build the index, and on simple
lookups GraphRAG-Bench shows plain RAG matching them.

## Research consulted

- Agent memory:
  - A-MEM: [arXiv 2502.12110](https://arxiv.org/abs/2502.12110)
  - MemGPT/Letta: [2310.08560](https://arxiv.org/abs/2310.08560); sleep-time: [2504.13171](https://arxiv.org/abs/2504.13171)
  - Mem0: [2504.19413](https://arxiv.org/abs/2504.19413)
  - Zep/Graphiti: [2501.13956](https://arxiv.org/abs/2501.13956)
  - Generative Agents: [2304.03442](https://arxiv.org/abs/2304.03442)
  - MemoryOS: [2506.06326](https://arxiv.org/abs/2506.06326)
  - LightMem: [2510.18866](https://arxiv.org/abs/2510.18866)
  - Nemori: [2508.03341](https://arxiv.org/abs/2508.03341)
  - MIRIX: [2507.07957](https://arxiv.org/abs/2507.07957)
  - LangMem: [repo](https://github.com/langchain-ai/langmem)
  - Benchmarks: LoCoMo [2402.17753](https://arxiv.org/abs/2402.17753), LongMemEval [2410.10813](https://arxiv.org/abs/2410.10813)
- RAG:
  - LinearRAG: [2510.10114](https://arxiv.org/abs/2510.10114)
  - HippoRAG 2: [2502.14802](https://arxiv.org/abs/2502.14802)
  - GraphRAG: [2404.16130](https://arxiv.org/abs/2404.16130)
  - LightRAG: [2410.05779](https://arxiv.org/abs/2410.05779)
  - RAPTOR: [2401.18059](https://arxiv.org/abs/2401.18059)
  - GraphRAG-Bench: [2506.05690](https://arxiv.org/abs/2506.05690)
  - [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)
- Context management:
  - ReadAgent: [2402.09727](https://arxiv.org/abs/2402.09727)
  - ReSum: [2509.13313](https://arxiv.org/abs/2509.13313)
  - Context-Folding: [2510.11967](https://arxiv.org/abs/2510.11967)
  - Anthropic, [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
  - Manus, [Context engineering lessons](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
  - Codex compaction (`codex-rs/core/src/compact.rs`)

## Not done yet

- **No memory UI for session notes or past-chat search.** The Settings page
  still shows only the personal-memory summary and notes.
- **No in-house evaluation.** The planned set is LongMemEval-style questions
  over MatOpt chats: notation recall, knowledge updates, abstention. It
  should exist before dense retrieval or graph defaults change.
- **Large tool outputs are not offloaded to the index when written.** They
  are still trimmed by the existing output limits.
