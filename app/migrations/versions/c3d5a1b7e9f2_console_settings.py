"""console settings, one key/value row per knob

The module set for the next room used to live in the operator's browser. It
belongs on the server: it is an operational decision, and a cleared cache must
not quietly restore the full show on the venue machine.

Revision ID: c3d5a1b7e9f2
Revises: b8c1e4f7a920
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d5a1b7e9f2"
down_revision = "b8c1e4f7a920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=48), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
