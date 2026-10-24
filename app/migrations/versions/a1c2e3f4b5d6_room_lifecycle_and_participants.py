"""room lifecycle and participants

Rooms become one-shot rounds: finishing a room closes it, and the next round
is a brand new room. This drops the free-text `label` column, adds `closed_at`
so the "current" room is the newest still-open one, and records everyone who
ever joined a room in `room_participants` so a closed round still remembers
who played in it.

Revision ID: a1c2e3f4b5d6
Revises: 7b3e4d8f1a2c
Create Date: 2026-10-24 22:40:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c2e3f4b5d6'
down_revision: Union[str, Sequence[str], None] = '7b3e4d8f1a2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('rooms', 'label')
    op.add_column(
        'rooms',
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'room_participants',
        sa.Column('room_code', sa.String(length=32), nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column(
            'joined_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['room_code'], ['rooms.code'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('room_code', 'player_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('room_participants')
    op.drop_column('rooms', 'closed_at')
    op.add_column('rooms', sa.Column('label', sa.String(length=80), nullable=True))
