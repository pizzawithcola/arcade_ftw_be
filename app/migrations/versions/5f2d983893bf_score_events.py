"""score events

Records every accepted score report so a retrying kiosk or a replayed node
cannot pay twice. The composite primary key is the whole mechanism: the insert
is `ON CONFLICT DO NOTHING`, and only a row that was actually inserted moves the
player's section total.

Revision ID: 5f2d983893bf
Revises: e8b3d5c1a712
Create Date: 2026-10-22 09:41:05.275432
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f2d983893bf'
down_revision: Union[str, Sequence[str], None] = 'e8b3d5c1a712'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'score_events',
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('event_key', sa.String(length=80), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('player_id', 'event_key'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('score_events')
