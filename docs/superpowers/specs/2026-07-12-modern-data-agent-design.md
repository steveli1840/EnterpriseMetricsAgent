# Modern Data Agent Core Design

## Purpose

EnterpriseMetricsAgent is already shaped like a governed data-agent product: it has a React workbench, FastAPI, LangGraph, metric YAML, a business glossary, ClickHouse, PostgreSQL with pgvector, and SQL guardrails. The next step is to make the runtime match that product shape.

This design focuses the first productionization phase on the backend agent core. The frontend remains mostly intact and will consume richer real API responses once the backend state and retrieval path are real.

## Goals

- Make the agent use the control plane at runtime instead of hard-coded aliases.
- Retrieve metric, schema, glossary, and user-memory context through a hybrid retriever.
- Keep SQL generation governed by metric compilation, not free-form SQL generation.
- Persist conversations, user memories, and query audit records in PostgreSQL.
- Make LangGraph nodes represent real workflow boundaries.
- Preserve read-only SQL enforcement and evidence-first responses.

## Non-Goals

- Do not introduce Vanna as the core framework.
- Do not use Deep Agents for the governed query path.
- Do not build a full OAuth/OIDC integration in this phase.
- Do not add arbitrary SQL generation for non-metric questions.
- Do not redesign the frontend visual system in this phase.
- Do not implement multi-warehouse federation beyond preserving the existing ClickHouse and BigQuery gateway boundaries.

## Recommended Approach

Use LangGraph as the primary orchestration layer and keep deterministic metric compilation as the SQL authority. The agent should retrieve context, resolve intent, compile governed SQL, validate it, execute it, and persist evidence. LLMs may help produce structured intent, but they should not own final SQL shape.

This keeps the system aligned with a production data-agent model:

```text
auth_context
  -> load_conversation
  -> retrieve_context
  -> resolve_intent
  -> compile_metric_sql
  -> validate_sql
  -> execute_query
  -> persist_audit
  -> build_answer
```

## Architecture

### API Layer

`backend/app/main.py` should stop owning in-memory dictionaries for conversations and memories. It should depend on repository/service objects backed by SQLAlchemy sessions.

Responsibilities:

- Authenticate demo JWTs for local development.
- Pass `tenant_id`, `user_id`, `role`, `conversation_id`, and request metadata into the agent.
- Stream progress events from graph node boundaries.
- Expose governance endpoints backed by PostgreSQL records.

### Control Plane Repository

Add a repository module, for example `backend/app/infrastructure/control_plane.py`, responsible for:

- Loading published metric definitions from `metric_registry`.
- Falling back to YAML metrics only in explicit test/bootstrap paths.
- Loading latest schema snapshots.
- Searching `knowledge_chunks` by keyword.
- Searching `knowledge_chunks` by vector when an embedding provider is available.
- Reading and writing `Conversation`, `UserMemory`, and `QueryAudit`.

The repository should enforce tenant and user filters. Every query for user-scoped data must include `tenant_id` and `user_id`.

### Hybrid Retrieval

Upgrade `backend/app/domain/retrieval.py` from an RRF helper into a retriever service that returns structured context:

```python
RetrievedContext(
    metrics=[...],
    schema=[...],
    knowledge=[...],
    memories=[...],
    fused_ids=[...],
)
```

Retrieval should combine:

- Exact and fuzzy metric name/label matching.
- Keyword search over metric/schema/glossary chunks.
- pgvector search over `KnowledgeChunk.embedding`.
- Confirmed user memories scoped by user and tenant.
- Reciprocal Rank Fusion for merged ranking.

If the embedding provider is unavailable, the retriever should degrade to keyword + exact matching and mark vector retrieval as unavailable in evidence or warnings.

### Agent Runtime

Refactor `GovernedAgentRuntime` so graph nodes do meaningful work rather than delegating everything to `AgentService.analyze()`.

Required nodes:

- `authorize`: validate `user_id` and tenant context.
- `load_conversation`: load or create a persisted conversation.
- `retrieve_context`: run hybrid retrieval for the question.
- `resolve_intent`: resolve metric, dimensions, time window, and ambiguity state.
- `compile_metric_sql`: compile SQL from the selected metric definition.
- `validate_sql`: enforce one read-only authorized query with SQLGlot.
- `execute_query`: run explain and execute through the query gateway.
- `persist_audit`: write normalized SQL, evidence, user, query id, and trace id.
- `build_answer`: return `AnalysisResult`.

The graph state should be explicit. Avoid storing opaque mutable dictionaries except for small evidence payloads.

### Intent Resolution

Intent resolution should prefer deterministic control-plane evidence:

1. Candidate metrics from retrieval.
2. Metric aliases, labels, descriptions, and glossary hits.
3. Allowed dimensions from selected metric definitions.
4. Time expressions resolved to a bounded date window.
5. Optional LLM JSON output constrained to retrieved metrics and dimensions.

If no metric or multiple strong metrics match, return a clarification event instead of guessing.

### SQL Compilation and Guardrails

Keep `compile_metric_query()` as the only normal path to final SQL.

Rules:

- Only published metrics can be queried.
- Only allowed dimensions can be grouped.
- Metric filters are always applied.
- Time window is always applied.
- `validate_read_only_sql()` still enforces a single `SELECT` or `WITH SELECT`.
- Authorized tables come from selected metric models and retrieved schema evidence.

### Persistence

Replace current in-memory state in `main.py`:

- `conversations: dict[str, list[dict]]`
- `memories_by_user: dict[str, list[dict]]`

with PostgreSQL-backed operations using the existing SQLAlchemy models:

- `Conversation`
- `UserMemory`
- `QueryAudit`

Conversation records should preserve enough state to show history and continue a session. Full message persistence can start simple: append user question and final result metadata inside `Conversation.state`.

### Evidence

Every successful response must expose:

- Metric name, version, label, owner.
- Schema refs and latest schema snapshot hash when available.
- Knowledge refs used by retrieval.
- User-memory refs when used.
- Fixed filters.
- Time window.
- Validated SQL.
- Query id, row count, elapsed time.
- Trace id.

If vector retrieval, LLM parsing, or schema snapshot lookup is unavailable, return a warning rather than silently hiding it.

## Frontend Impact

The first phase should keep the current React shell. The existing pages can move from static rows to real API data incrementally:

- Analysis page consumes richer `AnalysisResult` without redesign.
- Conversations page/sidebar uses persisted `/api/v1/conversations`.
- Knowledge page uses `/api/v1/knowledge`.
- Memories page uses `/api/v1/memories`.
- Audit page uses `/api/v1/audit/queries`.

No visual redesign is required for this phase.

## Testing Strategy

Backend tests should prove the new backend core, not just endpoint availability.

Required coverage:

- Hybrid retrieval fuses exact, keyword, vector, and memory results.
- Retriever degrades when embeddings are unavailable.
- Metric resolution uses retrieved metric/glossary context instead of hard-coded aliases.
- Ambiguous metric questions return clarification.
- Conversation records persist per user and do not leak across users.
- User memories persist per user and must be confirmed before retrieval.
- Query audit is written after successful execution.
- SQL guard still blocks mutations, multi-statements, and unauthorized tables.
- Chat stream emits progress and final result.

Existing frontend tests can remain focused on smoke behavior until backend endpoints stabilize.

## Delivery Plan

1. Add PostgreSQL-backed control-plane repository and dependency wiring.
2. Replace in-memory conversation and memory API state.
3. Implement hybrid retriever using current `KnowledgeChunk` and `UserMemory` models.
4. Refactor LangGraph runtime into real nodes.
5. Update `AgentService` to consume retrieved context and persist audit evidence.
6. Add backend tests for retrieval, persistence, graph flow, and audit.
7. Lightly adapt frontend API calls only where response shapes change.

## Acceptance Criteria

- The app can answer a governed Olist metric question through the LangGraph path.
- The selected metric comes from metric registry/retrieval context, not only hard-coded aliases.
- The response includes SQL and evidence refs for metric, schema, knowledge, filters, time window, and execution.
- Conversations and memories survive across API object lifetimes when backed by PostgreSQL.
- Query audit records are written for successful queries.
- Tests cover the listed behavior.
- No implementation depends on Vanna or Deep Agents as the core runtime.
