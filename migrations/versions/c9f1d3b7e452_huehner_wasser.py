"""Hühner: Wasser bei der täglichen Fütterung

Revision ID: c9f1d3b7e452
Revises: b4e8a2c6d731
"""
import sqlalchemy as sa
from alembic import op

revision = "c9f1d3b7e452"
down_revision = "b4e8a2c6d731"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("feedings") as batch:
        batch.add_column(sa.Column("water", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.execute("DELETE FROM feedings WHERE NOT wheat AND NOT mash")      # nur Wasser – gäbe es vorher nicht
    with op.batch_alter_table("feedings") as batch:
        batch.drop_column("water")
