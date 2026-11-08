"""Add paper session and event evidence without modifying existing records."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
    )
    op.create_table(
        "paper_events",
        sa.Column(
            "session_id", sa.String(64), sa.ForeignKey("paper_sessions.id"), primary_key=True
        ),
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_book_observations_received_id", "book_observations", ["received_at", "id"])


def downgrade() -> None:
    # Explicitly refuse destructive rollback of paper research evidence.
    raise RuntimeError("Archive paper evidence and review an explicit manual rollback first")
