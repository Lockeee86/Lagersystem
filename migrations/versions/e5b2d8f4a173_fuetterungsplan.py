"""Fütterungsplan der Biogasanlage: Tage (1×/2×) und Fütterungen je Person

Revision ID: e5b2d8f4a173
Revises: d1a7c3e5b820
"""
from alembic import op
import sqlalchemy as sa

revision = "e5b2d8f4a173"
down_revision = "d1a7c3e5b820"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feeding_days",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("times", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.String(length=200)),
        sa.Column("updated_by_id", sa.Integer()),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], name="fk_feeding_days_updated_by_id_users",
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("day", name="pk_feeding_days"),
    )
    op.create_table(
        "feeding_shifts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("done_by_id", sa.Integer()),
        sa.Column("note", sa.String(length=300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_feeding_shifts_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["done_by_id"], ["users.id"], name="fk_feeding_shifts_done_by_id_users",
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_feeding_shifts"),
        sa.UniqueConstraint("day", "slot", name="uq_feeding_shifts_day_slot"),
    )
    op.create_index("ix_feeding_shifts_day", "feeding_shifts", ["day"])
    op.create_index("ix_feeding_shifts_user_id", "feeding_shifts", ["user_id"])


def downgrade():
    op.drop_index("ix_feeding_shifts_user_id", table_name="feeding_shifts")
    op.drop_index("ix_feeding_shifts_day", table_name="feeding_shifts")
    op.drop_table("feeding_shifts")
    op.drop_table("feeding_days")
