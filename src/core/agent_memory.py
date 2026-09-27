"""
OpenClaw-style agent memory for Cuttle.
Layered memory: short-term (recent turns), episodic (conversation history),
semantic (vector), reflection, with summarization and a memory router.
"""

import json
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Under src/data/agent_memory/<identity>/
DATA_DIR = Path(__file__).parent.parent / "data"
MEMORY_ROOT = DATA_DIR / "agent_memory"

# Context budgets (~4 chars/token)
DEFAULT_MAX_CONTEXT_CHARS = 3200
DEFAULT_RECENT_TURNS = 5
MAX_ENTRY_CHARS = 400
SEMANTIC_MAX_CHARS = 800
REFLECTION_MAX_CHARS = 400
SUMMARY_MAX_CHARS = 600

# Thresholds
EPISODIC_SUMMARY_THRESHOLD = 80  # Trigger LLM summarization when older entries exceed this
REFLECTION_INTERVAL = 20  # Consider reflection every N exchanges


def _sanitize_identity(identity: str) -> str:
    """Safe directory name from identity."""
    if not identity or not identity.strip():
        return "default"
    s = re.sub(r"[^\w\-]", "_", identity.strip())
    return s[:96] or "default"


def _get_episodic_path(identity: str) -> Path:
    """Path to episodic memory JSON for this identity."""
    safe = _sanitize_identity(identity)
    return MEMORY_ROOT / safe / "episodic.json"


def _get_summaries_path(identity: str) -> Path:
    """Path to LLM-generated summaries of older episodic chunks."""
    safe = _sanitize_identity(identity)
    return MEMORY_ROOT / safe / "summaries.json"


def _get_reflection_path(identity: str) -> Path:
    """Path to reflection memory (lessons learned)."""
    safe = _sanitize_identity(identity)
    return MEMORY_ROOT / safe / "reflection.json"


def _ensure_memory_dir(identity: str) -> Path:
    """Create memory directory for identity. Returns directory path."""
    safe = _sanitize_identity(identity)
    path = MEMORY_ROOT / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


# -----------------------------------------------------------------------------
# Episodic memory
# -----------------------------------------------------------------------------


def append(
    identity: str,
    role: str,
    content: str,
    query_id: Optional[str] = None,
) -> bool:
    """
    Append a message to episodic memory.
    role: 'user' | 'assistant'
    """
    if not identity or not content or not content.strip():
        return False
    path = _get_episodic_path(identity)
    _ensure_memory_dir(identity)
    entry = {
        "role": role,
        "content": (content or "").strip(),
        "timestamp": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "query_id": query_id or "",
    }
    try:
        entries = []
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        entries.append(entry)
        if len(entries) > 500:
            entries = entries[-400:]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[AgentMemory] append error: {e}")
        return False


# -----------------------------------------------------------------------------
# Semantic (vector) memory - optional, requires chromadb + embeddings
# -----------------------------------------------------------------------------

_semantic_client = None
_semantic_ef = None


def _get_semantic_client():
    """Lazy-init Chroma client and embedding function. Returns (client, collection_name) or (None, None)."""
    global _semantic_client, _semantic_ef
    if _semantic_client is not None:
        return _semantic_client, _semantic_ef
    try:
        import chromadb
        from chromadb.config import Settings

        persist_dir = str(MEMORY_ROOT / "_chroma")
        client = chromadb.PersistentClient(path=persist_dir, settings=Settings(anonymized_telemetry=False))
        ef = None
        # Prefer local embeddings (sentence-transformers)
        try:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        except Exception:
            pass
        if ef is None and __import__("os").environ.get("OPENAI_API_KEY"):
            try:
                from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
                ef = OpenAIEmbeddingFunction()
            except Exception:
                pass
        if ef is None:
            return None, None
        _semantic_client = client
        _semantic_ef = ef
        return client, ef
    except Exception as e:
        print(f"[AgentMemory] Semantic memory unavailable: {e}")
        return None, None


def semantic_add(identity: str, text: str, metadata: Optional[Dict] = None) -> bool:
    """Add text to semantic memory. No-op if Chroma unavailable."""
    client, ef = _get_semantic_client()
    if not client or not ef:
        return False
    try:
        safe = _sanitize_identity(identity)
        coll = client.get_or_create_collection(name=f"agent_mem_{safe}", embedding_function=ef)
        doc_id = f"doc_{identity}_{int(time.time() * 1000)}_{hash(text) % 10**8}"
        coll.add(ids=[doc_id], documents=[text[:8000]], metadatas=[metadata or {}])
        return True
    except Exception as e:
        print(f"[AgentMemory] semantic_add error: {e}")
        return False


def semantic_search(identity: str, query: str, top_k: int = 4) -> List[str]:
    """Retrieve most relevant texts for query. Returns [] if Chroma unavailable."""
    if not query or not query.strip():
        return []
    client, ef = _get_semantic_client()
    if not client or not ef:
        return []
    try:
        safe = _sanitize_identity(identity)
        coll = client.get_or_create_collection(name=f"agent_mem_{safe}", embedding_function=ef)
        res = coll.query(query_texts=[query[:2000]], n_results=top_k)
        docs = res.get("documents", [[]])
        return docs[0] if docs else []
    except Exception as e:
        print(f"[AgentMemory] semantic_search error: {e}")
        return []


# -----------------------------------------------------------------------------
# Reflection memory
# -----------------------------------------------------------------------------


def store_reflection(identity: str, content: str) -> bool:
    """Store a distilled lesson (what worked, what failed, what to remember)."""
    if not identity or not content or not content.strip():
        return False
    path = _get_reflection_path(identity)
    _ensure_memory_dir(identity)
    entry = {
        "content": (content or "").strip(),
        "timestamp": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }
    try:
        entries = []
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        entries.append(entry)
        if len(entries) > 100:
            entries = entries[-80:]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        # Also add to semantic for long-term recall
        semantic_add(identity, f"[Reflection] {content}")
        return True
    except Exception as e:
        print(f"[AgentMemory] store_reflection error: {e}")
        return False


def _load_reflections(identity: str, max_chars: int = REFLECTION_MAX_CHARS) -> str:
    path = _get_reflection_path(identity)
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except Exception:
        return ""
    if not entries:
        return ""
    lines = ["## Reflections (lessons learned)\n"]
    for e in reversed(entries[-5:]):
        c = (e.get("content") or "").strip()
        if c:
            lines.append(f"- {c[:200]}{'...' if len(c) > 200 else ''}")
    blob = "\n".join(lines)
    return blob[:max_chars] + ("..." if len(blob) > max_chars else "")


# -----------------------------------------------------------------------------
# LLM summarization
# -----------------------------------------------------------------------------


def _call_llm_for_summary(text: str) -> Optional[str]:
    """Call LLM API to summarize text. Returns None on failure."""
    try:
        import requests
        resp = requests.post(
            "http://localhost:8080/api/llm-request",
            json={
                "nodeType": "llm-openai",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "prompt": f"Summarize the following conversation history concisely. Preserve key facts, decisions, and outcomes. Output only the summary, no preamble.\n\n---\n{text[:12000]}",
                "systemPrompt": "You are a summarizer. Output concise summaries only.",
                "temperature": 0.3,
                "maxTokens": 500,
            },
            timeout=30,
        )
        if resp.ok:
            data = resp.json()
            return (data.get("response") or "").strip()
    except Exception as e:
        print(f"[AgentMemory] LLM summary request failed: {e}")
    return None


def _trigger_summarization(identity: str) -> None:
    """Background: summarize oldest episodic entries and store. Non-blocking."""
    def _run():
        try:
            path = _get_episodic_path(identity)
            sum_path = _get_summaries_path(identity)
            if not path.exists():
                return
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            older_count = max(0, len(entries) - DEFAULT_RECENT_TURNS * 2)
            if older_count < EPISODIC_SUMMARY_THRESHOLD:
                return
            to_summarize = entries[: min(60, older_count)]
            text_parts = []
            for e in to_summarize:
                role = e.get("role", "user")
                c = (e.get("content") or "").strip()
                if c:
                    text_parts.append(f"{role}: {c[:500]}")
            if not text_parts:
                return
            raw = "\n".join(text_parts[-40:])
            summary = _call_llm_for_summary(raw)
            if not summary:
                return
            _ensure_memory_dir(identity)
            summaries = []
            if sum_path.exists():
                with open(sum_path, "r", encoding="utf-8") as f:
                    summaries = json.load(f)
            summaries.append({
                "range": f"0-{len(to_summarize)}",
                "summary": summary,
                "timestamp": time.time(),
            })
            if len(summaries) > 5:
                summaries = summaries[-4:]
            with open(sum_path, "w", encoding="utf-8") as f:
                json.dump(summaries, f, indent=2, ensure_ascii=False)
            # Prune summarized entries from episodic (keep recent)
            keep = entries[len(to_summarize):]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(keep, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[AgentMemory] summarization error: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def _load_summaries(identity: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    path = _get_summaries_path(identity)
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            summaries = json.load(f)
    except Exception:
        return ""
    if not summaries:
        return ""
    parts = [s.get("summary", "") for s in summaries[-2:] if s.get("summary")]
    blob = "\n".join(parts)
    return blob[:max_chars] + ("..." if len(blob) > max_chars else "")


# -----------------------------------------------------------------------------
# Memory router & get_context
# -----------------------------------------------------------------------------


def get_context(
    identity: str,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    recent_turns: int = DEFAULT_RECENT_TURNS,
    query: Optional[str] = None,
    use_semantic: bool = True,
    use_reflection: bool = True,
) -> str:
    """
    Build memory context string for injection into system prompt.
    Memory router: episodic (recent + condensed/summary) + optional semantic + reflection.
    query: Current user message for semantic vector search (improves retrieval).
    """
    text, _sources = get_context_with_sources(
        identity,
        max_chars=max_chars,
        recent_turns=recent_turns,
        query=query,
        use_semantic=use_semantic,
        use_reflection=use_reflection,
    )
    return text


def get_context_with_sources(
    identity: str,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    recent_turns: int = DEFAULT_RECENT_TURNS,
    query: Optional[str] = None,
    use_semantic: bool = True,
    use_reflection: bool = True,
) -> Tuple[str, List[Dict]]:
    """
    Same as get_context, plus a list of knowledge sources that contributed
    ({name, path, kind, content}) for query-log visibility.
    """
    if not identity:
        return "", []

    sections = []
    sources: List[Dict] = []
    remaining = max_chars

    # 1. Episodic: recent turns
    path = _get_episodic_path(identity)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception as e:
            print(f"[AgentMemory] get_context read error: {e}")
            entries = []
    else:
        entries = []

    keep_raw = min(recent_turns * 2, len(entries))
    raw_entries = entries[-keep_raw:]
    older = entries[:-keep_raw] if len(entries) > keep_raw else []

    lines = ["## Recent context (from prior turns)\n"]
    for e in raw_entries:
        role = e.get("role", "user")
        label = "User" if role == "user" else "Assistant"
        content = (e.get("content") or "").strip()
        if content:
            lines.append(f"**{label}:** {content[:1500]}{'...' if len(content) > 1500 else ''}\n")
    episodic_recent = "\n".join(lines)
    if raw_entries and any((e.get("content") or "").strip() for e in raw_entries):
        sections.append(episodic_recent)
        remaining -= len(episodic_recent)
        sources.append({
            "name": "episodic.json",
            "path": str(path).replace("\\", "/"),
            "kind": "agent_memory",
            "content": episodic_recent.strip(),
        })

    # 2. Older context: summaries first, else condensed
    if remaining > 100 and older:
        summary_blob = _load_summaries(identity, max_chars=min(SUMMARY_MAX_CHARS, remaining - 50))
        if summary_blob:
            older_summary_section = "\n## Older context (summarized)\n" + summary_blob
            sections.append(older_summary_section)
            remaining -= len(summary_blob) + 50
            summaries_path = _get_summaries_path(identity)
            sources.append({
                "name": "summaries.json",
                "path": str(summaries_path).replace("\\", "/"),
                "kind": "agent_memory",
                "content": older_summary_section.strip(),
            })
        if remaining > 150:
            summary_parts = []
            for e in older[-20:]:
                role = e.get("role", "user")
                c = (e.get("content") or "").strip()
                if c:
                    summary_parts.append(f"- {role}: {c[:MAX_ENTRY_CHARS]}{'...' if len(c) > MAX_ENTRY_CHARS else ''}")
            if summary_parts:
                older_blob = "\n".join(summary_parts[-8:])
                if len(older_blob) > remaining - 80:
                    older_blob = older_blob[: remaining - 80] + "..."
                older_condensed = "\n## Older context (condensed)\n" + older_blob
                sections.append(older_condensed)
                # Same underlying file as recent episodic; only add once if not already listed
                if not any(s.get("name") == "episodic.json" for s in sources):
                    sources.append({
                        "name": "episodic.json",
                        "path": str(path).replace("\\", "/"),
                        "kind": "agent_memory",
                        "content": older_condensed.strip(),
                    })
                else:
                    # Append condensed excerpt to existing episodic source content
                    for s in sources:
                        if s.get("name") == "episodic.json":
                            s["content"] = (s.get("content") or "").rstrip() + "\n\n" + older_condensed.strip()
                            break
            # Trigger async summarization when threshold exceeded
            if len(older) >= EPISODIC_SUMMARY_THRESHOLD:
                _trigger_summarization(identity)

    # 3. Semantic (vector search) - only if query provided
    if use_semantic and query and remaining > 200:
        hits = semantic_search(identity, query, top_k=3)
        if hits:
            sem_lines = ["\n## Relevant past context (retrieved)\n"]
            for h in hits:
                sem_lines.append(f"- {h[:300]}{'...' if len(h) > 300 else ''}")
            sem_blob = "\n".join(sem_lines)
            if len(sem_blob) <= remaining - 50:
                sections.append(sem_blob)
                sources.append({
                    "name": "semantic (retrieved)",
                    "path": str(MEMORY_ROOT / "_chroma").replace("\\", "/"),
                    "kind": "agent_memory",
                    "content": sem_blob.strip(),
                })

    # 4. Reflection
    if use_reflection and remaining > 150:
        refl = _load_reflections(identity, max_chars=min(REFLECTION_MAX_CHARS, remaining - 50))
        if refl:
            sections.append("\n" + refl)
            refl_path = _get_reflection_path(identity)
            sources.append({
                "name": "reflection.json",
                "path": str(refl_path).replace("\\", "/"),
                "kind": "agent_memory",
                "content": refl.strip(),
            })

    result = "\n".join(sections).strip()
    if len(result) > max_chars:
        return result[:max_chars] + "\n\n[... truncated]", sources
    return result, sources


def resolve_identity(
    session_data: Optional[Dict],
    pipeline_name: Optional[str],
) -> str:
    """
    Resolve memory identity from session and pipeline.
    Scheduled runs: per-pipeline (Play, Think, etc.).
    User sessions: per routing_key (web_anon_xyz, discord_guild_123).
    """
    if not session_data:
        return pipeline_name or "default"
    kind = session_data.get("session_kind") or ""
    routing_key = (session_data.get("routing_key") or "").strip()
    if kind == "schedule" and pipeline_name:
        return f"pipeline_{_sanitize_identity(pipeline_name)}"
    return routing_key or pipeline_name or "default"


def store_exchange(
    identity: str,
    user_message: str,
    assistant_response: str,
    query_id: Optional[str] = None,
) -> None:
    """Store a complete user+assistant exchange in episodic memory and semantic memory."""
    if not identity:
        return
    append(identity, "user", user_message, query_id)
    append(identity, "assistant", (assistant_response or "").strip(), query_id)
    # Add to semantic for long-term retrieval (combined turn)
    combined = f"User: {user_message[:2000]}\nAssistant: {(assistant_response or '')[:2000]}"
    semantic_add(identity, combined, metadata={"type": "exchange", "query_id": query_id or ""})
    _maybe_trigger_reflection(identity)


def _default_reflection_via_llm(identity: str, recent_context: str) -> Optional[str]:
    """Use LLM to distill a lesson from recent context. Returns None on failure."""
    try:
        import requests
        resp = requests.post(
            "http://localhost:8080/api/llm-request",
            json={
                "nodeType": "llm-openai",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "prompt": f"Review this recent conversation. In 1-2 short sentences, state what worked, what failed, or what should be remembered for future turns. Output only the lesson.\n\n---\n{recent_context[:3000]}",
                "systemPrompt": "You are a reflection assistant. Output concise lessons only.",
                "temperature": 0.4,
                "maxTokens": 150,
            },
            timeout=20,
        )
        if resp.ok:
            data = resp.json()
            return (data.get("response") or "").strip()
    except Exception as e:
        print(f"[AgentMemory] Reflection LLM failed: {e}")
    return None


def run_reflection(
    identity: str,
    reflection_fn: Optional[Callable[[str, str], Optional[str]]] = None,
) -> bool:
    """
    Run a reflection step: pass recent user+assistant to reflection_fn,
    store the returned lesson. Call from pipeline after N exchanges.
    reflection_fn(identity, recent_context) -> "lesson string" or None.
    If None, uses default LLM-based reflection.
    """
    ctx = get_context(identity, max_chars=2000, recent_turns=3, use_semantic=False, use_reflection=False)
    if not ctx or len(ctx) < 100:
        return False
    fn = reflection_fn or _default_reflection_via_llm
    lesson = fn(identity, ctx)
    if lesson and lesson.strip():
        return store_reflection(identity, lesson)
    return False


def _maybe_trigger_reflection(identity: str) -> None:
    """If exchange count is a multiple of REFLECTION_INTERVAL, run reflection in background."""
    def _run():
        try:
            path = _get_episodic_path(identity)
            if not path.exists():
                return
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            assistant_count = sum(1 for e in entries if e.get("role") == "assistant")
            if assistant_count > 0 and assistant_count % REFLECTION_INTERVAL == 0:
                run_reflection(identity)
        except Exception as e:
            print(f"[AgentMemory] reflection trigger error: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
