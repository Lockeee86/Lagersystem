"""Benutzer „nur Bestattungswald“

Revision ID: b6d0f4c8e752
Revises: a5c9e3b7d641
"""
from alembic import op
import sqlalchemy as sa

revision = "b6d0f4c8e752"
down_revision = "a5c9e3b7d641"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("forest_only", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("forest_only")
