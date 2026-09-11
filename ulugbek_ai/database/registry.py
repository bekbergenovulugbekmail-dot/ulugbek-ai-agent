"""Single import point for every ORM model.

``Base.metadata`` is only complete once all model modules have been imported.
Alembic's ``env.py``, ``Database.create_all`` and the test fixtures all call
:func:`import_all_models` so none of them can silently miss a table.
"""

from __future__ import annotations

import importlib
from typing import Final

MODEL_MODULES: Final[tuple[str, ...]] = (
    "ulugbek_ai.identity.models",
    "ulugbek_ai.projects.models",
    "ulugbek_ai.tasks.models",
    "ulugbek_ai.memory.models",
    "ulugbek_ai.agent.models",
    "ulugbek_ai.tools.models",
    "ulugbek_ai.approvals.models",
)


def import_all_models() -> None:
    """Import every model module so the metadata is fully populated."""
    for module in MODEL_MODULES:
        importlib.import_module(module)
