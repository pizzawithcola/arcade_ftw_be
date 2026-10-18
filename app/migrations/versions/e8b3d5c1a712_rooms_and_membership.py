"""rooms exist as data, and players belong to one

Adds the table the in-memory registry has been pretending to be, seeds the room
that already exists, and lets a player record which room they are playing in.

A player's room is nullable: the row exists from login, and membership only
becomes real when they enter a room. Deleting a room empties the membership
rather than deleting the people.

Revision ID: e8b3d5c1a712
Revises: c4f1a9b7e2d5
Create Date: 2026-10-18 19:11:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b3d5c1a712'
down_revision: Union[str, Sequence[str], None] = 'c4f1a9b7e2d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rooms',
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('event', sa.String(length=32), nullable=True),
        sa.Column('label', sa.String(length=80), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('code'),
    )

    # The deployment has only ever run one room; it becomes real data here so the
    # registry and the database agree from the first migration onwards.
    op.execute(
        "insert into rooms (code, event, label) values ('main', null, 'Main show') "
        "on conflict (code) do nothing"
    )

    op.add_column('players', sa.Column('room_code', sa.String(length=32), nullable=True))
    op.create_foreign_key(
        'players_room_code_fkey',
        'players',
        'rooms',
        ['room_code'],
        ['code'],
        ondelete='SET NULL',
    )
    op.create_index('ix_players_room_code', 'players', ['room_code'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_players_room_code', table_name='players')
    op.drop_constraint('players_room_code_fkey', 'players', type_='foreignkey')
    op.drop_column('players', 'room_code')
    op.drop_table('rooms')
