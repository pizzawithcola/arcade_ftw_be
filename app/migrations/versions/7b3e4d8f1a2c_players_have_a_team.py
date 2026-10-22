"""players have a team

Sabotage splits a room's players into two teams. The assignment is a property of
the player-in-this-room, and a player is in at most one room, so a single
nullable column is enough: it is set by the host's assign call and cleared when
the player joins a different room.

Revision ID: 7b3e4d8f1a2c
Revises: 5f2d983893bf
Create Date: 2026-10-22 12:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b3e4d8f1a2c'
down_revision: Union[str, Sequence[str], None] = '5f2d983893bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('players', sa.Column('team', sa.String(length=8), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('players', 'team')
