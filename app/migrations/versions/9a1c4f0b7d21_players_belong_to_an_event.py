"""players belong to an event

Replaces the unused `country` column with the event a player signed in for, and
moves uniqueness from the nickname alone to (event, nickname).

Revision ID: 9a1c4f0b7d21
Revises: 0d85f32b238d
Create Date: 2026-10-13 23:58:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a1c4f0b7d21'
down_revision: Union[str, Sequence[str], None] = '0d85f32b238d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Rename rather than drop-and-add: `country` was never read or written by
    # anything but a throwaway test, and keeping the column means any existing
    # player row survives with its id intact.
    op.alter_column(
        'players',
        'country',
        new_column_name='event',
        existing_type=sa.String(length=8),
        type_=sa.String(length=32),
        existing_nullable=True,
    )

    # Backfill before tightening, so this cannot fail on a database that already
    # has players. 'legacy' is deliberately not one of the selectable events: it
    # marks rows that predate the field and can never be produced by the API.
    op.execute("UPDATE players SET event = 'legacy' WHERE event IS NULL")
    op.alter_column('players', 'event', existing_type=sa.String(length=32), nullable=False)

    # A nickname is only unique within its own event now.
    op.drop_index(op.f('ix_players_nickname'), table_name='players')
    op.create_unique_constraint('uq_players_event_nickname', 'players', ['event', 'nickname'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_players_event_nickname', 'players', type_='unique')
    op.create_index(op.f('ix_players_nickname'), 'players', ['nickname'], unique=True)
    # Back to nullable first: the old shape allowed a null country.
    op.alter_column('players', 'event', existing_type=sa.String(length=32), nullable=True)
    op.alter_column(
        'players',
        'event',
        new_column_name='country',
        existing_type=sa.String(length=32),
        type_=sa.String(length=8),
        existing_nullable=True,
    )
