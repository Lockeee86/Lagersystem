"""Fütterungsplan: Mengen in Kilogramm je Substrat

Revision ID: c3f9a7d2e164
Revises: b6e2f8a1d473
"""
from alembic import op
import sqlalchemy as sa

revision = "c3f9a7d2e164"
down_revision = "b6e2f8a1d473"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feeding_substrates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "feeding_amounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("feeding_shifts.id", name="fk_feeding_amounts_shift",
                                                          ondelete="CASCADE"), nullable=False),
        sa.Column("substrate_id", sa.Integer(), sa.ForeignKey("feeding_substrates.id", name="fk_feeding_amounts_substrate"),
                  nullable=False),
        sa.Column("kg", sa.Integer(), nullable=False),
        sa.UniqueConstraint("shift_id", "substrate_id", name="uq_feeding_amounts_shift_substrate"),
    )
    op.create_index("ix_feeding_amounts_shift_id", "feeding_amounts", ["shift_id"])
    op.create_index("ix_feeding_amounts_substrate_id", "feeding_amounts", ["substrate_id"])


def downgrade():
    op.drop_index("ix_feeding_amounts_substrate_id", table_name="feeding_amounts")
    op.drop_index("ix_feeding_amounts_shift_id", table_name="feeding_amounts")
    op.drop_table("feeding_amounts")
    op.drop_table("feeding_substrates")
