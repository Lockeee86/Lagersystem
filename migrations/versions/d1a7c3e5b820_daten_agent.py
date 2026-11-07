"""Daten-Agent: Fragen mit Antwort, Werkzeugen und Kosten

Revision ID: d1a7c3e5b820
Revises: c4e8a2d6f913
"""
from alembic import op
import sqlalchemy as sa

revision = "d1a7c3e5b820"
down_revision = "c4e8a2d6f913"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assistant_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer()),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="laeuft"),
        sa.Column("error", sa.String(length=20)),
        sa.Column("tools", sa.Text()),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_assistant_queries_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_assistant_queries"),
    )
    op.create_index("ix_assistant_queries_user_id", "assistant_queries", ["user_id"])
    op.create_index("ix_assistant_queries_created_at", "assistant_queries", ["created_at"])


def downgrade():
    op.drop_index("ix_assistant_queries_created_at", table_name="assistant_queries")
    op.drop_index("ix_assistant_queries_user_id", table_name="assistant_queries")
    op.drop_table("assistant_queries")
