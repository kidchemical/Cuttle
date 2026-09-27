# Agent Memory (OpenClaw-Style)

Cuttle uses a layered memory model inspired by OpenClaw to manage context and avoid context-window explosion.

## Architecture

### 1. Short-Term / Episodic Memory
- **Last 5 turns** (user + assistant pairs) injected in full into the LLM system prompt
- Stored in `src/data/agent_memory/<identity>/episodic.json`
- Each entry: `{role, content, timestamp, query_id}`
- Capped at 500 entries; older entries are summarized or condensed

### 2. LLM Summarization
- When older entries exceed **80**, the oldest 60 are summarized via LLM (gpt-4o-mini)
- Summaries stored in `summaries.json`
- Summarized entries are pruned from episodic to keep it lean
- Runs in a background thread; uses `/api/llm-request`

### 3. Semantic (Vector) Memory
- Long-term knowledge via embeddings + ChromaDB
- **Embeddings**: sentence-transformers (all-MiniLM-L6-v2) by default, or OpenAI if no local model
- **Storage**: ChromaDB in `src/data/agent_memory/_chroma/`
- Exchanges and reflections are indexed for retrieval
- **Retrieval**: Top 3–4 similar chunks injected when the current query is provided
- Requires `chromadb` and `sentence-transformers` (or `OPENAI_API_KEY` for OpenAI embeddings)

### 4. Reflection Memory
- Distilled lessons: "what worked", "what failed", "what to remember"
- Stored in `reflection.json`
- **Trigger**: Every 20 exchanges, an LLM reflection step runs in the background
- Reflections are also added to semantic memory for long-term recall

### 5. Memory Router
The `get_context()` function orchestrates what to fetch:

| Layer      | When Included                         | Budget    |
|------------|---------------------------------------|-----------|
| Episodic   | Always (recent + condensed/summary)   | ~2800 chars |
| Semantic   | When `query` provided                 | ~800 chars |
| Reflection | Always (if any)                        | ~400 chars |

Order: Recent → Summarized (oldest) → Condensed (middle) → Semantic (retrieved) → Reflection

## Identity Resolution

| Trigger Type   | Identity             | Example                    |
|----------------|----------------------|----------------------------|
| Schedule       | `pipeline_{name}`    | `pipeline_Play`, `pipeline_Think` |
| Web chat       | `routing_key`        | `web_anon_xyz`, `web_user_42`     |
| Discord        | `routing_key`        | `discord_guild_123`        |

Scheduled pipelines (Play, Think, Self-Improvement, Cuttle Main) each maintain their own memory.

## Integration Points

- **Pipeline executor** (`_execute_llm_node`): Injects memory with `query=prompt` for semantic retrieval
- **Pipeline completion**: Stores exchange via `store_exchange()`; triggers reflection when interval reached
- **Tool-remote-agent LLM fallback**: Injects memory with `query=message`

## Dependencies

- **chromadb** (required for semantic memory)
- **sentence-transformers** (local embeddings; optional — falls back to OpenAI if `OPENAI_API_KEY` set)
- **OpenAI API** (for summarization and reflection when using gpt-4o-mini)

## Procedural Memory (Future)

Skills and procedures could live in workspace memory (AGENTS.md) or a dedicated skills DB. The current router does not yet route to procedural memory.
