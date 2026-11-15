"""Zündung je Zylinder: Kerzen, Stecker, Spulen mit Laufzeit, Wechsel und Tausch

Revision ID: b4e8a2c6d731
Revises: a7d3e9b5c218
"""
import sqlalchemy as sa
from alembic import op

revision = "b4e8a2c6d731"
down_revision = "a7d3e9b5c218"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("machines") as batch:
        for name in ("cylinders", "cylinder_banks", "ign_plug_hours", "ign_cap_hours", "ign_coil_hours"):
            batch.add_column(sa.Column(name, sa.Integer(), nullable=True))
    op.create_table(
        "ignition_parts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("cylinder", sa.Integer(), nullable=True),
        sa.Column("installed_on", sa.Date(), nullable=False),
        sa.Column("installed_counter", sa.Integer(), nullable=False),
        sa.Column("removed_on", sa.Date(), nullable=True),
        sa.Column("removed_counter", sa.Integer(), nullable=True),
        sa.Column("removed_reason", sa.String(length=10), nullable=True),
        sa.Column("last_cylinder", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], name="fk_ignition_parts_machine", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_ignition_parts"),
    )
    op.create_index("ix_ignition_parts_machine_id", "ignition_parts", ["machine_id"])
    op.create_table(
        "ignition_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("counter", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("cylinder", sa.Integer(), nullable=False),
        sa.Column("other_cylinder", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=10), nullable=True),
        sa.Column("life_hours", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], name="fk_ignition_events_machine", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_ignition_events_user", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_ignition_events"),
    )
    op.create_index("ix_ignition_events_machine_id", "ignition_events", ["machine_id"])


def downgrade():
    op.drop_index("ix_ignition_events_machine_id", table_name="ignition_events")
    op.drop_table("ignition_events")
    op.drop_index("ix_ignition_parts_machine_id", table_name="ignition_parts")
    op.drop_table("ignition_parts")
    with op.batch_alter_table("machines") as batch:
        for name in ("ign_coil_hours", "ign_cap_hours", "ign_plug_hours", "cylinder_banks", "cylinders"):
            batch.drop_column(name)
