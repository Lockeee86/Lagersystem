"""Meldungen zum Nachlesen, Schriftgröße, Erste Schritte

Revision ID: a4d7e1c9b352
Revises: f2c9a4e7d651
"""
from alembic import op
import sqlalchemy as sa

revision = "a4d7e1c9b352"
down_revision = "f2c9a4e7d651"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("font_size", sa.String(10), nullable=False, server_default=""))
        batch.add_column(sa.Column("onboarded", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_notices_user", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("topic", sa.String(40)),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(500)),
        sa.Column("url", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_notices_user_id", "notices", ["user_id"])
    op.create_index("ix_notices_created_at", "notices", ["created_at"])


def downgrade():
    op.drop_index("ix_notices_created_at", table_name="notices")
    op.drop_index("ix_notices_user_id", table_name="notices")
    op.drop_table("notices")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("onboarded")
        batch.drop_column("font_size")
