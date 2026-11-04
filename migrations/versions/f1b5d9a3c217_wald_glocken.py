"""Bestattungswald: Kirchenglocken je Beisetzung

Revision ID: f1b5d9a3c217
Revises: e9a3c7f1b085
"""
from alembic import op
import sqlalchemy as sa

revision = "f1b5d9a3c217"
down_revision = "e9a3c7f1b085"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("forest_burials") as batch:
        batch.add_column(sa.Column("bells", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("forest_burials") as batch:
        batch.drop_column("bells")
