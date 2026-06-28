"""
memory_tools.py - Dual memory system for Omni-Dev

PRIMARY:   SimpleMemory (JSON file) — always works, zero cloud dependencies
SECONDARY: Cognee graph memory — best effort, skipped silently on any error

This ensures the agent NEVER loses memory due to cloud API failures.
"""
import asyncio
from typing import Any, Dict

from src.simple_memory import remember as sm_remember, recall as sm_recall
from .base_tool import BaseTool


class MemoryWriteTool(BaseTool):
    """
    Store a fact or context into long-term memory.
    Uses SimpleMemory (JSON file) as primary + Cognee as secondary.
    """

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a fact, user preference, or project context into long-term memory. "
            "Use this to persist important information across sessions. "
            "ALWAYS call this after building something, completing a task, or learning a user preference. "
            "The information can be retrieved later using 'recall'."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "fact": {
                "type": "string",
                "description": "The fact, preference, or context to remember permanently.",
            },
        }

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, fact: str, **kwargs) -> str:
        """Store the fact in memory (SimpleMemory primary + Cognee secondary)."""
        if not fact or not fact.strip():
            return "Error: fact parameter is required."

        # PRIMARY: SimpleMemory — always works
        ok = sm_remember(fact.strip())

        # SECONDARY: Cognee (best effort, silent fail)
        cognee_ok = False
        try:
            import cognee
            try:
                await cognee.remember(fact.strip(), dataset_name="user_memory")
                cognee_ok = True
            except Exception:
                try:
                    await cognee.add(fact.strip(), dataset_name="user_memory")
                    await cognee.cognify()
                    cognee_ok = True
                except Exception:
                    pass
        except Exception:
            pass

        if ok:
            suffix = " (+ Cognee graph)" if cognee_ok else " (Cognee graph unavailable — JSON memory used)"
            return f"✅ Fact saved to long-term memory{suffix}."
        return "❌ Error: could not save to memory."


class MemoryReadTool(BaseTool):
    """
    Search long-term memory for past context.
    Uses SimpleMemory (JSON file) as primary + Cognee as secondary.
    """

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search long-term memory for past context, facts, or user preferences. "
            "Use this at the START of every session to load past work context. "
            "Use this when the user references past work or when you need project history. "
            "Returns insights from memory storage."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "query": {
                "type": "string",
                "description": "What you want to search for in long-term memory.",
            },
        }

    def is_read_only(self) -> bool:
        return True

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, query: str, **kwargs) -> str:
        """Search memory using SimpleMemory first, then Cognee."""
        if not query or not query.strip():
            return "Error: query parameter is required."

        results = []

        # PRIMARY: SimpleMemory — always works
        sm_results = sm_recall(query.strip(), top_k=8)
        results.extend(sm_results)

        # SECONDARY: Cognee (best effort, silent fail)
        try:
            import cognee
            from cognee.modules.search.types.SearchType import SearchType
            try:
                # Use CHUNKS search type — no LLM call needed, just vector similarity
                cog_results = await cognee.search(
                    query_text=query.strip(),
                    query_type=SearchType.CHUNKS,
                )
            except Exception:
                try:
                    cog_results = await cognee.recall(query_text=query.strip())
                except Exception:
                    cog_results = []

            for res in cog_results:
                text = None
                for attr in ("answer", "text", "content", "summary", "description", "fact", "node_name"):
                    text = getattr(res, attr, None)
                    if text and isinstance(text, str) and text.strip():
                        break
                if not text:
                    text = str(res)
                if text and text.strip() and text.strip() not in results:
                    results.append(text.strip())
        except Exception:
            pass

        if results:
            return "📚 **Memory Retrieved:**\n\n" + "\n\n---\n\n".join(results[:10])
        return "No relevant memories found. (Memory is fresh — start working and memories will be stored automatically.)"
