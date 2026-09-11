"""Project business logic, including routing a request to a project."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.errors import ConflictError, NotFoundError
from ulugbek_ai.projects.models import Project
from ulugbek_ai.projects.repository import ProjectRepository
from ulugbek_ai.projects.schemas import ProjectCreate, ProjectUpdate, slugify

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Weights used when routing a free-text request to a project.
_NAME_MATCH_SCORE = 5.0
_SLUG_MATCH_SCORE = 5.0
_KEYWORD_MATCH_SCORE = 3.0
_TOKEN_MATCH_SCORE = 1.0
#: A project must beat this to be auto-selected; below it the run stays global.
_MIN_ROUTING_SCORE = 3.0


@dataclass(slots=True)
class ProjectMatch:
    """A routing candidate and why it was chosen."""

    project: Project
    score: float
    reason: str


def _tokenize(text: str) -> set[str]:
    return {token for token in _WORD_RE.findall(text.lower()) if len(token) > 2}


class ProjectManager:
    """Create, update and resolve projects."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = ProjectRepository(session)

    # ------------------------------------------------------------------ CRUD #
    async def create(
        self, payload: ProjectCreate, *, owner_id: uuid.UUID | None = None
    ) -> Project:
        slug = payload.slug or slugify(payload.name)
        if await self._repository.get_by_slug(slug) is not None:
            raise ConflictError(
                f"A project with slug '{slug}' already exists.",
                details={"slug": slug},
            )
        project = Project(
            name=payload.name,
            slug=slug,
            description=payload.description,
            status=payload.status,
            repository=payload.repository,
            environment=payload.environment,
            owner_id=owner_id,
            keywords=payload.keywords,
            integrations=payload.integrations,
            extra=payload.extra,
        )
        self._repository.add(project)
        await self._repository.flush()
        return project

    async def get(self, project_id: uuid.UUID) -> Project:
        project = await self._repository.get(project_id)
        if project is None:
            raise NotFoundError(
                f"Project {project_id} not found.",
                details={"project_id": str(project_id)},
            )
        return project

    async def list(self, **kwargs) -> list[Project]:
        return await self._repository.list(**kwargs)

    async def update(self, project_id: uuid.UUID, payload: ProjectUpdate) -> Project:
        project = await self.get(project_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        await self._repository.flush()
        return project

    # --------------------------------------------------------------- routing #
    async def resolve(
        self, text: str, *, project_id: uuid.UUID | None = None
    ) -> ProjectMatch | None:
        """Pick the project a request refers to.

        An explicit ``project_id`` always wins. Otherwise candidates are scored
        on name, slug and keyword overlap; a request that matches nothing stays
        project-less rather than being forced into an arbitrary project.
        """
        if project_id is not None:
            project = await self.get(project_id)
            return ProjectMatch(project=project, score=100.0, reason="explicit")

        candidates = await self._repository.list_active()
        if not candidates:
            return None

        lowered = text.lower()
        tokens = _tokenize(text)
        best: ProjectMatch | None = None

        for project in candidates:
            score = 0.0
            reasons: list[str] = []

            if project.name.lower() in lowered:
                score += _NAME_MATCH_SCORE
                reasons.append(f"name '{project.name}'")
            if project.slug in lowered:
                score += _SLUG_MATCH_SCORE
                reasons.append(f"slug '{project.slug}'")

            matched_keywords = [
                keyword
                for keyword in project.keywords
                if keyword and keyword.lower() in lowered
            ]
            if matched_keywords:
                score += _KEYWORD_MATCH_SCORE * len(matched_keywords)
                reasons.append(f"keywords {matched_keywords}")

            overlap = tokens & _tokenize(project.name)
            if overlap:
                score += _TOKEN_MATCH_SCORE * len(overlap)
                reasons.append(f"tokens {sorted(overlap)}")

            if score > 0 and (best is None or score > best.score):
                best = ProjectMatch(
                    project=project, score=score, reason=", ".join(reasons)
                )

        if best is not None and best.score >= _MIN_ROUTING_SCORE:
            return best
        return None
