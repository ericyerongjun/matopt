# Tool discovery: implementation and local experiment

Measured 10 October 2026. This is an offline retrieval and dispatch-contract experiment. It does not measure model task success, answer correctness, provider latency, or user experience. Raw results and every query outcome are in [tool-discovery-benchmark.json](reference/tool-discovery-benchmark.json).

## Decision

Use exact dispatch for all catalogs. Advertise all visible tools for catalogs of at most 32 tools; for larger catalogs retain visible native tools and use a metadata BM25 shortlist of up to eight remote tools. Keep every explicitly selected, skill-required, or explicitly loaded tool in addition to that shortlist. Visibility and capability gates still apply.

The candidate improved retrieval on these fixtures and eliminated unsafe fuzzy dispatch. It **increased initial schema bytes compared with the previous lexical shortlist**. The small-catalog path trades a larger prompt for full availability without a discovery round trip. The threshold of 32 and shortlist of eight are initial engineering defaults, not globally optimal values. Re-evaluate them on actual configured catalogs and paired model tasks before claiming an end-to-end improvement.

## Implemented contract

`backend/app/agent/tools/router.py` separates execution resolution from retrieval:

- `resolve(registry, name)` accepts an exact registered ID or an existing explicit native alias. Exact IDs take precedence over aliases. Aliases retain case-insensitive compatibility. A typo, punctuation-normalized ID, or fuzzy match only produces suggestions; it never selects a handler for execution.
- `search(registry, query, limit=8, *, ctx=None)` returns ranked metadata without calling any tool's `schema()` or handler. It indexes names, descriptions, server names, and optional `discovery_keywords`, with BM25 term weighting, simple plural normalization, and a small auditable synonym vocabulary. Zero-evidence queries return no hits; there is no alphabetical relevance fallback.
- `load_schemas(registry, names, *, ctx=None)` validates the entire selection before accessing schemas. Unknown or unavailable names fail atomically. Only the selected tools' schemas are read. Returned names are canonical.
- `schemas(registry, query, plan=None, preferred_tools=None, *, loaded_tools=None, ctx=None)` implements the small-catalog path and larger-catalog shortlist. Pins are additional to the shortlist budget, never merely assigned a ranking bonus. Invalid names cannot become implicit selections.
- Metadata indexing is cached, rebuilt when tool identity/name/description/server/keywords change, and filtered by current visibility flags. The cache retains one catalog to bound memory.

`backend/app/agent/tools/discovery_tool.py` exposes two native tools:

1. **`search_tools`**: accepts a capability/service query and optional limit 1–20. Returns metadata and canonical names, never schemas or execution results.
2. **`load_tool_schemas`**: accepts 1–20 exact names. Returns parameter schemas, appends canonical names to `ctx.state['loaded_tools']`, and sets `ToolResult.meta['refresh_tools'] = True`. It changes run state, so it is not declared read-only or safe for parallel execution.

The runtime registers both tools against the same registry. The harness passes `loaded_tools` and `ctx` into `current_schemas()`, then refreshes schemas after a successful `refresh_tools` result before the next provider request. Loading schemas neither executes a tool nor overrides disabled capabilities. A focused actual-harness test verifies search → load → refreshed request → one exact invocation.

This is **lazy schema materialization and prompt inclusion**. Existing MCP startup still receives remote `inputSchema` values while listing tools. This change does not implement lazy network fetching, MCP pagination, or remote catalog-change notifications.

## Experiment setup

Run from the repository root:

```sh
backend/.venv/bin/python scripts/benchmark_tool_discovery.py \
  --output docs/reference/tool-discovery-benchmark.json
```

Python 3.12.14, macOS 27.0.1, ARM64. Deterministic expansion seed: `104729`. The report records the fixture SHA-256. Each catalog has four native tools; the 30-tool base includes 26 realistic connector capabilities. Larger catalogs add seeded action/entity/service combinations with deliberately similar names and descriptions. Schemas have the same parameter structure to isolate selection behavior.

There are 30 hand-authored queries: 10 literal requests, 10 paraphrases, six confusing-name/negation requests, and four changes of task direction. Both methods receive the same current query in these comparisons. Pin retention is a separate 12-selection test. Unknown dispatch uses 10 malformed IDs; valid dispatch uses 30 exact IDs and two explicit aliases.

The frozen baseline reproduces the previous router: all native tools plus lexical-overlap top eight MCP tools, three alphabetical MCP tools when nothing matches, pins assigned a score bonus inside the budget, punctuation normalization, and single fuzzy-match execution. The candidate uses the implementation above. No tool executes and no model, external service, credentials, or private user data is used.

## Results

“Initial recall” is the fraction of queries whose designated expected tool appears in the offered schemas. “Discovery recall@8” measures the metadata search result directly. These are retrieval metrics; other returned tools may be irrelevant.

| Catalog | Baseline initial recall | Candidate initial recall | Candidate discovery recall@8 | Baseline mean schema bytes | Candidate mean schema bytes |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 30 | 27/30 (90.0%) | 30/30 (100%) | 30/30 (100%) | 4,073 | 14,118 |
| 300 | 25/30 (83.3%) | 29/30 (96.7%) | 29/30 (96.7%) | 4,738 | 6,369 |
| 3,000 | 25/30 (83.3%) | 29/30 (96.7%) | 29/30 (96.7%) | 4,759 | 6,375 |

Byte counts use compact UTF-8 JSON, **not tokens**. Candidate counts include both discovery-tool schemas; baseline counts do not. Full-catalog schema bytes were 12,894 / 139,868 / 1,409,614 respectively. These are initial-request costs only: discovery tool results and extra provider requests can add further cost. The candidate reduces prompt size relative to advertising every large-catalog schema, but increases it relative to the previous shortlist.

For every catalog size:

| Contract check | Baseline | Candidate |
| --- | ---: | ---: |
| Unknown-ID probes that silently resolve to an executable tool | 7/10 | 0/10 |
| Correct exact-ID / explicit-alias resolution | 32/32 | 32/32 |
| Explicit pins retained | 8/12 | 12/12 |

No handler was executed for these dispatch measurements: the metric is whether the resolver would choose one. The candidate additionally has a structural exact-ID/alias guard, tested separately from these ten probes.

Warm latency uses three passes over all 30 queries (90 samples per cell). It excludes provider/network calls and JSON transmission. Baseline selection times its name filtering; candidate selection includes constructing the selected schema dictionaries, so these are local router measurements rather than identical serialization pipelines.

| Catalog | Baseline selection median / p95 ms | Candidate selection median / p95 ms | Candidate metadata search median / p95 ms | Cold index + search ms |
| ---: | ---: | ---: | ---: | ---: |
| 30 | 0.065 / 0.071 | 0.009 / 0.012 | 0.027 / 0.036 | 0.384 |
| 300 | 1.022 / 1.062 | 0.256 / 0.462 | 0.210 / 0.415 | 2.919 |
| 3,000 | 11.174 / 11.368 | 2.642 / 5.468 | 2.147 / 4.906 | 29.656 |

The first measurement exposed a regex-cache threshold effect at 3,000 tools. Replacing per-tool regex compilation with one query-token pass reduced repeated lookup overhead. The table and JSON contain the final implementation's measurements. The cold number is one observation, not a distribution; latencies vary with machine load.

## Failures and evidence limits

- At 300 and 3,000 tools, the candidate missed **“Read words in a photo” → `vision_extract_text`**. Generic “read” matches outweighed the weak photo/image expansion. This failure remains in the fixtures; it was not patched with a query-specific rule. A user/model can refine the query to OCR or explicitly load a known ID.
- Paraphrase recall on larger catalogs improved from 5/10 to 9/10; literal, confusing-name, and mid-task groups were 100% recall for both methods there. Recall@8 does not prove the correct tool ranked first, that negation was understood, or that a model would choose it.
- Fixtures are synthetic, English-only, small in query diversity, and not a held-out production evaluation. The synonym vocabulary and query cases were authored in the same work session. Results support this implementation choice locally, not broad semantic-retrieval superiority.
- Uniform schema shapes do not model real schema sizes or MCP network failures. Large native catalogs still stay fully advertised. Many selected pins intentionally grow the prompt rather than silently dropping requested capabilities.
- No embeddings, reranker, learned routing, task success, cost-to-correct-answer, or user-perceived latency was evaluated. Those are follow-up experiments, not claimed outcomes.

## Verification

```sh
backend/.venv/bin/python -m pytest \
  backend/tests/test_tool_discovery.py backend/tests/test_skills.py -q
```

**32 passed.** Coverage includes exact dispatch, alias precedence, bounded suggestions, small-catalog availability, pinned/loaded/skill retention, lazy schema access, catalog changes, capability gates, malformed arguments, atomic loading, and actual-harness schema refresh. Broad backend regression is owned by the parent integration task.
