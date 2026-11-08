"""Create append-only validated observations and opportunities."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "book_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_book_observations_observed_at", "book_observations", ["observed_at"])
    op.create_table(
        "opportunities",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_opportunities_observed_at", "opportunities", ["observed_at"])


def downgrade() -> None:
    op.drop_table("opportunities")
    op.drop_table("book_observations")
