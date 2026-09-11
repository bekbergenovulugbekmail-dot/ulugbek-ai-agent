"""Memory operations used by the agent and the API."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import MemoryType
from ulugbek_ai.core.errors import NotFoundError
from ulugbek_ai.core.redaction import redact_text, truncate
from ulugbek_ai.llm.base import LLMClient, LLMMessage
from ulugbek_ai.memory.models import Memory
from ulugbek_ai.memory.repository import MemoryRepository
from ulugbek_ai.memory.schemas import MemoryCreate, MemoryUpdate
from ulugbek_ai.memory.search import (
    KeywordSearchStrategy,
    MemorySearchStrategy,
    ScoredMemory,
)

#: Cap on a single stored memory, so one huge tool result cannot dominate.
MAX_MEMORY_CHARS = 20_000

_SUMMARY_SYSTEM_PROMPT = (
    "You compress an agent's memories into a single dense paragraph. "
    "Keep concrete facts, decisions, names, identifiers and user preferences. "
    "Drop pleasantries and repetition. Never invent information."
)


class MemoryManager:
    """Save, search, update and summarize memories.

    Callers get *relevant* memories, never the whole table: :meth:`search` ranks
    a bounded candidate set and applies both a count and a character budget.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        strategy: MemorySearchStrategy | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self._session = session
        self._repository = MemoryRepository(session)
        self._strategy = strategy or KeywordSearchStrategy()
        self._llm = llm

    # ------------------------------------------------------------------ save #
    async def save_memory(self, payload: MemoryCreate) -> Memory:
        """Persist one memory. Content is redacted and length-capped first."""
        memory = Memory(
            type=payload.type,
            content=truncate(redact_text(payload.content), MAX_MEMORY_CHARS),
            summary=redact_text(payload.summary) if payload.summary else None,
            source=payload.source,
            importance=payload.importance,
            tags=[tag.strip().lower() for tag in payload.tags if tag.strip()],
            extra=payload.extra,
            user_id=payload.user_id,
            project_id=payload.project_id,
            task_id=payload.task_id,
        )
        self._repository.add(memory)
        await self._repository.flush()
        return memory

    async def remember(
        self,
        content: str,
        *,
        type: MemoryType,
        user_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        importance: float = 0.5,
        source: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> Memory:
        """Convenience wrapper around :meth:`save_memory`."""
        return await self.save_memory(
            MemoryCreate(
                type=type,
                content=content,
                importance=importance,
                source=source,
                tags=list(tags or []),
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
            )
        )

    # ----------------------------------------------------------------- fetch #
    async def get_memory(self, memory_id: uuid.UUID) -> Memory:
        memory = await self._repository.get(memory_id)
        if memory is None:
            raise NotFoundError(
                f"Memory {memory_id} not found.",
                details={"memory_id": str(memory_id)},
            )
        return memory

    async def list_memories(self, **kwargs) -> list[Memory]:
        return await self._repository.list(**kwargs)

    async def search_memory(
        self,
        query: str,
        *,
        user_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        types: Sequence[MemoryType] | None = None,
        limit: int = 10,
        max_chars: int | None = None,
        touch: bool = True,
    ) -> list[ScoredMemory]:
        """Rank memories against *query* within the given scope.

        Args:
            max_chars: Optional character budget; results are cut off once the
                accumulated content would exceed it.
            touch: Record retrieval on the returned rows (access counters).
        """
        terms = self._strategy.query_terms(query)
        if not terms:
            return []

        candidates = await self._repository.find_candidates(
            terms, user_id=user_id, project_id=project_id, types=types
        )
        ranked = self._strategy.rank(query, candidates, limit=limit)

        if max_chars is not None:
            ranked = self._apply_char_budget(ranked, max_chars)

        if touch:
            for item in ranked:
                item.memory.touch()
            await self._repository.flush()

        return ranked

    @staticmethod
    def _apply_char_budget(
        ranked: list[ScoredMemory], max_chars: int
    ) -> list[ScoredMemory]:
        selected: list[ScoredMemory] = []
        used = 0
        for item in ranked:
            length = len(item.memory.content)
            if used + length > max_chars and selected:
                break
            selected.append(item)
            used += length
        return selected

    # ---------------------------------------------------------------- update #
    async def update_memory(
        self, memory_id: uuid.UUID, payload: MemoryUpdate
    ) -> Memory:
        memory = await self.get_memory(memory_id)
        updates = payload.model_dump(exclude_unset=True)
        if "content" in updates and updates["content"] is not None:
            updates["content"] = truncate(
                redact_text(updates["content"]), MAX_MEMORY_CHARS
            )
        if updates.get("summary"):
            updates["summary"] = redact_text(updates["summary"])
        for field, value in updates.items():
            setattr(memory, field, value)
        await self._repository.flush()
        return memory

    async def delete_memory(self, memory_id: uuid.UUID) -> None:
        memory = await self.get_memory(memory_id)
        await self._repository.delete(memory)

    # ------------------------------------------------------------- summarize #
    async def summarize_memory(
        self,
        *,
        project_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        types: Sequence[MemoryType] | None = None,
        limit: int = 50,
        store: bool = True,
    ) -> Memory | None:
        """Condense a scope's memories into one ``CONVERSATION_SUMMARY`` row.

        Returns ``None`` when there is nothing to summarize. Requires an LLM
        client; construct the manager with one to use this.
        """
        if self._llm is None:
            raise NotFoundError(
                "summarize_memory requires an LLM client. Construct "
                "MemoryManager(session, llm=...) to enable it."
            )

        memories = await self._repository.list(
            types=types, user_id=user_id, project_id=project_id, limit=limit
        )
        if not memories:
            return None

        joined = "\n".join(
            f"- [{memory.type}] {memory.content}" for memory in memories
        )
        response = await self._llm.complete(
            [LLMMessage.user(f"Memories to compress:\n{joined}")],
            system=_SUMMARY_SYSTEM_PROMPT,
            max_tokens=2_000,
        )
        summary_text = response.text.strip()
        if not summary_text:
            return None

        if not store:
            return Memory(
                type=MemoryType.CONVERSATION_SUMMARY,
                content=summary_text,
                user_id=user_id,
                project_id=project_id,
            )

        return await self.save_memory(
            MemoryCreate(
                type=MemoryType.CONVERSATION_SUMMARY,
                content=summary_text,
                importance=0.8,
                source="memory.summarize",
                user_id=user_id,
                project_id=project_id,
                extra={"summarized_count": len(memories)},
            )
        )
