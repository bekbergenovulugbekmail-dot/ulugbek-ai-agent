"""Record which external service a tool execution spoke to.

Additive and nullable: existing rows are local-tool executions, which have no
service. The column lets the UI group and filter integration activity without
consulting the tool registry.

Revision ID: 0002_tool_service
Revises: 0001_initial
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0002_tool_service'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('tool_executions', sa.Column('service', sa.String(length=50), nullable=True))
    op.create_index(op.f('ix_tool_executions_service'), 'tool_executions', ['service'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tool_executions_service'), table_name='tool_executions')
    op.drop_column('tool_executions', 'service')
