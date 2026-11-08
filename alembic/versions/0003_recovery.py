"""Transactional source ordering, immutable windows and recoverable paper checkpoints."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy rows remain untouched/NULL; they are not silently certified by this protocol.
    op.execute("CREATE SEQUENCE crossx_source_sequence")
    op.add_column("book_observations", sa.Column("source_sequence", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_book_observations_source_sequence",
        "book_observations",
        ["source_sequence"],
        unique=True,
    )
    for column in (
        sa.Column("checkpoint_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("recovery_state", postgresql.JSONB(), nullable=True),
        sa.Column("checkpoint_digest", sa.String(64), nullable=True),
        sa.Column("source_cursor", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="legacy"),
        sa.Column("checkpoint_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("paper_sessions", column)
    op.create_table(
        "paper_attempts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("paper_sessions.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
    )
    op.create_table(
        "paper_source_windows",
        sa.Column(
            "session_id", sa.String(64), sa.ForeignKey("paper_sessions.id"), primary_key=True
        ),
        sa.Column("version", sa.BigInteger(), primary_key=True),
        sa.Column("start_sequence", sa.BigInteger(), nullable=False),
        sa.Column("end_sequence", sa.BigInteger(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
    )
    statements = """
    CREATE FUNCTION crossx_order_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM pg_advisory_xact_lock(728406310427);
      NEW.source_sequence := nextval('crossx_source_sequence');
      RETURN NEW;
    END $$;
    -- next
    CREATE TRIGGER crossx_order_insert BEFORE INSERT ON book_observations
      FOR EACH ROW EXECUTE FUNCTION crossx_order_insert();
    -- next
    CREATE FUNCTION crossx_guard_frozen_book() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM pg_advisory_xact_lock(728406310427);
      IF EXISTS (SELECT 1 FROM paper_source_windows
          WHERE start_sequence < OLD.source_sequence AND end_sequence >= OLD.source_sequence) THEN
        RAISE EXCEPTION 'Observation belongs to an immutable paper source window';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      IF NEW.source_sequence IS DISTINCT FROM OLD.source_sequence THEN
        RAISE EXCEPTION 'Source sequence cannot be changed';
      END IF;
      RETURN NEW;
    END $$;
    -- next
    CREATE TRIGGER crossx_guard_frozen_book BEFORE UPDATE OR DELETE ON book_observations
      FOR EACH ROW EXECUTE FUNCTION crossx_guard_frozen_book();
    -- next
    CREATE FUNCTION crossx_guard_window() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Closed source windows are immutable'; END $$;
    -- next
    CREATE TRIGGER crossx_guard_window BEFORE UPDATE OR DELETE ON paper_source_windows
      FOR EACH ROW EXECUTE FUNCTION crossx_guard_window();
    """
    for statement in statements.split("-- next"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Recovery/source evidence requires an explicitly reviewed manual rollback")
