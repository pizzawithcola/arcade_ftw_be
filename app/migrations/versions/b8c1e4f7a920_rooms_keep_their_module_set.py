"""rooms keep their module set

A room is built from a set of modules (four games plus the welcome, the activity
board and the wrap-up), so a show can be run short without teaching the engine to
skip anything. The set is fixed when the room is created and never edited: the
engine's node list is built from it once, which is what keeps node indexes - and
therefore every score report keyed by one - stable for the life of the room.

NULL means every module. That is what all existing rooms mean, so the history
needs no backfill and keeps playing the same thirteen nodes it always did.

Revision ID: b8c1e4f7a920
Revises: a1c2e3f4b5d6
Create Date: 2026-11-14 20:40:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8c1e4f7a920'
down_revision: Union[str, Sequence[str], None] = 'a1c2e3f4b5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('rooms', sa.Column('modules', sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('rooms', 'modules')
