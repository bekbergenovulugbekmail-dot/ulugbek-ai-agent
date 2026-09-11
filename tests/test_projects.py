"""Projects: creation, slugs, and routing a free-text request to a project."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import ProjectStatus
from ulugbek_ai.core.errors import ConflictError, NotFoundError
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.schemas import ProjectCreate, ProjectUpdate, slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ERP System", "erp-system"),
        ("  Instagram   Automation ", "instagram-automation"),
        ("Bot v2.0", "bot-v2-0"),
        ("!!!", "project"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert slugify(name) == expected


async def test_create_and_read(session: AsyncSession) -> None:
    manager = ProjectManager(session)
    project = await manager.create(
        ProjectCreate(
            name="ERP",
            description="Warehouse and invoicing",
            repository="ulugbek/erp",
            keywords=["Invoice", "warehouse"],
            integrations={"github": {"repository": "ulugbek/erp"}},
        )
    )

    assert project.slug == "erp"
    assert project.keywords == ["invoice", "warehouse"]
    assert (await manager.get(project.id)).id == project.id


async def test_duplicate_slug_is_rejected(session: AsyncSession) -> None:
    manager = ProjectManager(session)
    await manager.create(ProjectCreate(name="ERP"))
    with pytest.raises(ConflictError):
        await manager.create(ProjectCreate(name="ERP"))


async def test_unknown_project(session: AsyncSession) -> None:
    import uuid

    with pytest.raises(NotFoundError):
        await ProjectManager(session).get(uuid.uuid4())


async def test_update(session: AsyncSession) -> None:
    manager = ProjectManager(session)
    project = await manager.create(ProjectCreate(name="Shop"))

    updated = await manager.update(
        project.id, ProjectUpdate(status=ProjectStatus.PAUSED, environment="staging")
    )

    assert updated.status is ProjectStatus.PAUSED
    assert updated.environment == "staging"


async def test_routing_by_explicit_id_wins(session: AsyncSession) -> None:
    manager = ProjectManager(session)
    erp = await manager.create(ProjectCreate(name="ERP"))
    await manager.create(ProjectCreate(name="Telegram Bot"))

    match = await manager.resolve("send a telegram message", project_id=erp.id)

    assert match is not None
    assert match.project.id == erp.id
    assert match.reason == "explicit"


async def test_routing_by_name_and_keyword(session: AsyncSession) -> None:
    manager = ProjectManager(session)
    await manager.create(ProjectCreate(name="ERP", keywords=["invoice"]))
    bot = await manager.create(
        ProjectCreate(name="Telegram Bot", keywords=["telegram", "chat"])
    )

    match = await manager.resolve("post a telegram update for customers")

    assert match is not None
    assert match.project.id == bot.id


async def test_an_unrelated_request_is_not_forced_into_a_project(
    session: AsyncSession,
) -> None:
    """Routing must be able to say 'none' rather than guess."""
    manager = ProjectManager(session)
    await manager.create(ProjectCreate(name="ERP", keywords=["invoice"]))

    assert await manager.resolve("what is the weather today") is None


async def test_paused_projects_are_not_routing_candidates(
    session: AsyncSession,
) -> None:
    manager = ProjectManager(session)
    project = await manager.create(ProjectCreate(name="Instagram", keywords=["instagram"]))
    await manager.update(project.id, ProjectUpdate(status=ProjectStatus.ARCHIVED))

    assert await manager.resolve("publish an instagram post") is None
