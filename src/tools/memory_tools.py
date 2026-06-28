"""
memory_tools.py - Fixed for Cognee 1.2.2 API

Cognee 1.2.2 changed the API:
  OLD (broken): cognee.search("SEARCH_TYPE_INSIGHTS", query_text=q)
  NEW (correct): cognee.recall(query_text=q)  or  cognee.search(query_text=q)

  OLD (works but slow): cognee.add(text) + cognee.cognify()
  NEW (preferred):      cognee.remember(text, dataset_name=...)
"""
import asyncio
from typing import Any, Dict

import cognee
from .base_tool import BaseTool


def _extract_recall_text(result) -> str:
    """Extract human-readable text from a cognee recall/search result object."""
    # Try common attribute names
    for attr in ("answer", "text", "content", "summary", "description", "fact", "node_name"):
        val = getattr(result, attr, None)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    # Dict-like
    if hasattr(result, "__dict__"):
        d = result.__dict__
        for k in ("answer", "text", "content", "summary", "description", "fact", "node_name"):
            if k in d and d[k]:
                return str(d[k]).strip()
    return str(result)


class MemoryWriteTool(BaseTool):
    """
    Store a fact or context into long-term Cognee graph memory.
    Uses the new Cognee 1.2.2 remember() API.
    """

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a fact, user preference, or project context into long-term Cognee graph memory. "
            "Use this to persist important information across sessions. "
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

    async def call(self, fact: str) -> str:
        """Store the fact in Cognee memory using the new remember() API."""
        if not fact or not fact.strip():
            return "Error: fact parameter is required."
        try:
            # Try new cognee.remember() API (v1.2.2+)
            await asyncio.to_thread(cognee.remember, fact, dataset_name="user_memory")
            return "✅ Fact successfully saved to long-term Cognee graph memory."
        except Exception as e1:
            try:
                # Fallback: old V1 API (add + cognify)
                await cognee.add(fact, dataset_name="user_memory")
                await cognee.cognify()
                return "✅ Fact saved to Cognee memory (via V1 API)."
            except Exception as e2:
                return f"Error saving to memory: {e2}"


class MemoryReadTool(BaseTool):
    """
    Search long-term Cognee graph memory for past context.
    Uses the new Cognee 1.2.2 recall() API.
    """

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search long-term Cognee graph memory for past context, facts, or user preferences. "
            "Use this at the START of every session to load past work context. "
            "Use this to retrieve information stored in previous sessions or by sub-agents. "
            "Returns insights from the knowledge graph."
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

    async def call(self, query: str) -> str:
        """Search Cognee memory using the new recall() API."""
        if not query or not query.strip():
            return "Error: query parameter is required."
        try:
            # Try new cognee.recall() API (v1.2.2+) - preferred
            results = await cognee.recall(query_text=query)
            if results:
                parts = []
                for res in results:
                    text = _extract_recall_text(res)
                    if text and text not in parts:
                        parts.append(text)
                if parts:
                    return "📚 **Memory Retrieved:**\n" + "\n\n".join(parts[:10])
            return "No relevant memories found in the Cognee knowledge graph."
        except Exception as e1:
            try:
                # Fallback: new-style search() with correct signature
                results = await cognee.search(query_text=query)
                if results:
                    parts = []
                    for res in results:
                        text = _extract_recall_text(res)
                        if text and text not in parts:
                            parts.append(text)
                    if parts:
                        return "📚 **Memory Retrieved:**\n" + "\n\n".join(parts[:10])
                return "No relevant memories found in the Cognee knowledge graph."
            except Exception as e2:
                return f"Error recalling from memory: {e2}"
