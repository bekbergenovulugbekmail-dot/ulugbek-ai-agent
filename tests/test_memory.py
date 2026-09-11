"""Memory: storage, scoping, relevance ranking and context budgeting."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import MemoryType
from ulugbek_ai.memory.manager import MemoryManager
from ulugbek_ai.memory.schemas import MemoryCreate, MemoryUpdate
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.schemas import ProjectCreate


async def test_saving_redacts_secrets(session: AsyncSession) -> None:
    manager = MemoryManager(session)
    memory = await manager.save_memory(
        MemoryCreate(
            type=MemoryType.FACT,
            content="The deploy token is ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        )
    )
    assert "ghp_" not in memory.content
    assert "REDACTED" in memory.content


async def test_search_ranks_by_relevance(session: AsyncSession) -> None:
    manager = MemoryManager(session)
    await manager.remember("Railway deployments run on the staging environment.", type=MemoryType.FACT)
    await manager.remember("The office cat is called Mosh.", type=MemoryType.FACT)
    await manager.remember("Railway project identifier is rw-42.", type=MemoryType.FACT)

    results = await manager.search_memory("railway deployment identifier")

    assert len(results) == 2
    assert all("Railway" in item.memory.content for item in results)
    assert results[0].score >= results[1].score


async def test_search_returns_nothing_for_an_unrelated_query(
    session: AsyncSession,
) -> None:
    manager = MemoryManager(session)
    await manager.remember("Railway project identifier is rw-42.", type=MemoryType.FACT)

    assert await manager.search_memory("photosynthesis in ferns") == []


async def test_search_is_scoped_to_a_project(session: AsyncSession) -> None:
    projects = ProjectManager(session)
    erp = await projects.create(ProjectCreate(name="ERP"))
    shop = await projects.create(ProjectCreate(name="Shop"))

    manager = MemoryManager(session)
    await manager.remember("Invoice numbering starts at 1000.", type=MemoryType.FACT, project_id=erp.id)
    await manager.remember("Invoice emails go out nightly.", type=MemoryType.FACT, project_id=shop.id)
    await manager.remember("Invoices are always in UZS.", type=MemoryType.FACT)

    results = await manager.search_memory("invoice", project_id=erp.id)
    contents = [item.memory.content for item in results]

    assert "Invoice numbering starts at 1000." in contents
    assert "Invoices are always in UZS." in contents  # global memories still apply
    assert "Invoice emails go out nightly." not in contents


async def test_search_respects_a_character_budget(session: AsyncSession) -> None:
    manager = MemoryManager(session)
    for index in range(10):
        await manager.remember(
            f"Deployment note {index}: " + "detail " * 30, type=MemoryType.FACT
        )

    results = await manager.search_memory("deployment note detail", max_chars=500)
    total = sum(len(item.memory.content) for item in results)

    assert results
    assert total <= 500 + len(results[0].memory.content)


async def test_retrieval_is_recorded(session: AsyncSession) -> None:
    manager = MemoryManager(session)
    memory = await manager.remember("Staging uses the eu-west region.", type=MemoryType.FACT)

    await manager.search_memory("staging region")

    refreshed = await manager.get_memory(memory.id)
    assert refreshed.access_count == 1
    assert refreshed.last_accessed_at is not None


async def test_update_and_delete(session: AsyncSession) -> None:
    manager = MemoryManager(session)
    memory = await manager.remember("Old fact.", type=MemoryType.FACT)

    updated = await manager.update_memory(
        memory.id, MemoryUpdate(content="New fact.", importance=0.9)
    )
    assert updated.content == "New fact."
    assert updated.importance == 0.9

    await manager.delete_memory(memory.id)
    assert await manager.list_memories() == []
