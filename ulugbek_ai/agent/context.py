"""Context assembly.

The rule this module exists to enforce: **never send the whole memory database
to the model**. A run's context is built from a small number of bounded,
relevant sections, under an explicit character budget.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.prompts import context_block
from ulugbek_ai.core.enums import MemoryType
from ulugbek_ai.core.redaction import truncate
from ulugbek_ai.memory.manager import MemoryManager
from ulugbek_ai.projects.models import Project
from ulugbek_ai.tasks.repository import TaskRepository

#: Memory types that describe *durable* context, always worth loading for a
#: project even when the wording of the request does not match them.
_ALWAYS_LOAD_TYPES = (
    MemoryType.PROJECT_CONTEXT,
    MemoryType.USER_CONTEXT,
    MemoryType.PREFERENCE,
    MemoryType.DECISION,
)


@dataclass(slots=True)
class RunContext:
    """The bounded context assembled for one run."""

    project: Project | None = None
    memories: list[str] = field(default_factory=list)
    recent_tasks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    char_count: int = 0

    def to_sections(self) -> dict[str, str]:
        sections: dict[str, str] = {}
        if self.project is not None:
            sections["project"] = _describe_project(self.project)
        if self.memories:
            sections["relevant_memory"] = "\n".join(
                f"- {item}" for item in self.memories
            )
        if self.recent_tasks:
            sections["recent_tasks"] = "\n".join(
                f"- {item}" for item in self.recent_tasks
            )
        if self.notes:
            sections["notes"] = "\n".join(f"- {item}" for item in self.notes)
        return sections

    def render(self) -> str:
        """The context as a prompt fragment."""
        return context_block(self.to_sections())

    def summary(self) -> dict[str, Any]:
        """Audit-friendly description of what was loaded (not the content)."""
        return {
            "project": str(self.project.id) if self.project else None,
            "memory_count": len(self.memories),
            "recent_task_count": len(self.recent_tasks),
            "char_count": self.char_count,
        }


def _describe_project(project: Project) -> str:
    lines = [
        f"name: {project.name}",
        f"slug: {project.slug}",
        f"status: {project.status}",
    ]
    if project.description:
        lines.append(f"description: {project.description}")
    if project.repository:
        lines.append(f"repository: {project.repository}")
    if project.environment:
        lines.append(f"environment: {project.environment}")
    if project.integrations:
        lines.append(f"integrations: {', '.join(sorted(project.integrations))}")
    return "\n".join(lines)


class ContextBuilder:
    """Loads exactly the context a run needs, and no more."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        memory_limit: int = 20,
        max_chars: int = 12_000,
    ) -> None:
        self._session = session
        self._memory_limit = memory_limit
        self._max_chars = max_chars
        self._memories = MemoryManager(session)
        self._tasks = TaskRepository(session)

    async def build(
        self,
        message: str,
        *,
        project: Project | None = None,
        user_id: uuid.UUID | None = None,
        include_recent_tasks: bool = True,
    ) -> RunContext:
        """Assemble the context for *message*.

        Two retrieval passes are merged and de-duplicated:

        1. relevance — memories matching the request text;
        2. durability — standing project/user context, preferences, decisions.

        The result is then cut to the character budget, highest relevance first.
        """
        context = RunContext(project=project)
        budget = self._max_chars
        seen: set[uuid.UUID] = set()
        collected: list[tuple[float, str, int]] = []

        relevant = await self._memories.search_memory(
            message,
            user_id=user_id,
            project_id=project.id if project else None,
            limit=self._memory_limit,
        )
        for item in relevant:
            if item.memory.id in seen:
                continue
            seen.add(item.memory.id)
            collected.append(
                (item.score, f"[{item.memory.type}] {item.memory.content}",
                 len(item.memory.content))
            )

        standing = await self._memories.list_memories(
            types=list(_ALWAYS_LOAD_TYPES),
            user_id=user_id,
            project_id=project.id if project else None,
            include_global=True,
            limit=self._memory_limit,
        )
        for memory in standing:
            if memory.id in seen:
                continue
            seen.add(memory.id)
            # Standing context ranks below an explicit relevance match but above
            # nothing; importance breaks ties among standing memories.
            collected.append(
                (float(memory.importance), f"[{memory.type}] {memory.content}",
                 len(memory.content))
            )

        collected.sort(key=lambda entry: entry[0], reverse=True)
        for _score, rendered, length in collected:
            if length > budget:
                continue
            context.memories.append(rendered)
            budget -= length

        if include_recent_tasks and project is not None:
            tasks = await self._tasks.list_recent_for_project(project.id, limit=5)
            for task in tasks:
                line = f"{task.status}: {truncate(task.goal, 200)}"
                if len(line) > budget:
                    break
                context.recent_tasks.append(line)
                budget -= len(line)

        context.char_count = self._max_chars - budget
        return context
