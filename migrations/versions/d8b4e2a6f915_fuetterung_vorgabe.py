"""Fütterungsplan: Vorgabe-Mengen (Standard je Substrat, abweichend je Fütterung)

Revision ID: d8b4e2a6f915
Revises: c3f9a7d2e164
"""
from alembic import op
import sqlalchemy as sa

revision = "d8b4e2a6f915"
down_revision = "c3f9a7d2e164"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("feeding_substrates") as batch:
        batch.add_column(sa.Column("target_kg", sa.Integer()))
    op.create_table(
        "feeding_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("substrate_id", sa.Integer(), sa.ForeignKey("feeding_substrates.id", name="fk_feeding_targets_substrate",
                                                              ondelete="CASCADE"), nullable=False),
        sa.Column("kg", sa.Integer(), nullable=False),
        sa.UniqueConstraint("day", "slot", "substrate_id", name="uq_feeding_targets_day_slot_substrate"),
    )
    op.create_index("ix_feeding_targets_day", "feeding_targets", ["day"])


def downgrade():
    op.drop_index("ix_feeding_targets_day", table_name="feeding_targets")
    op.drop_table("feeding_targets")
    with op.batch_alter_table("feeding_substrates") as batch:
        batch.drop_column("target_kg")
