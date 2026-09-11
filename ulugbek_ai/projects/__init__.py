"""Projects — the universal container the agent works *inside*.

A project is any addressable piece of the user's world: an ERP, a Telegram bot,
an Instagram account, a website, a repository. Integrations are attached through
the ``integrations`` mapping rather than through per-kind columns, so a new
integration never requires a migration.
"""

from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.models import Project
from ulugbek_ai.projects.repository import ProjectRepository

__all__ = ["Project", "ProjectManager", "ProjectRepository"]
