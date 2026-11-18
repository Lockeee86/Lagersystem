"""Bestellwunsch: Verknüpfung mit einer Aufgabe (Ersatzteil für einen Stillstand)

Revision ID: e8b2c4d6f915
Revises: d3a7f1c9b604
"""
import sqlalchemy as sa
from alembic import op

revision = "e8b2c4d6f915"
down_revision = "d3a7f1c9b604"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_requests") as batch:
        batch.add_column(sa.Column("task_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_purchase_requests_task_id", "tasks", ["task_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_purchase_requests_task_id", ["task_id"])


def downgrade():
    with op.batch_alter_table("purchase_requests") as batch:
        batch.drop_index("ix_purchase_requests_task_id")
        batch.drop_constraint("fk_purchase_requests_task_id", type_="foreignkey")
        batch.drop_column("task_id")
