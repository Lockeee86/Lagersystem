"""Bestattungswald: Umriss für Flächen (Waldruhefläche als Polygon aus dem GIS)

Revision ID: b7d3f1a9c542
Revises: a2c6e0b4d318
"""
from alembic import op
import sqlalchemy as sa

revision = "b7d3f1a9c542"
down_revision = "a2c6e0b4d318"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("forest_trees") as batch:
        batch.add_column(sa.Column("outline", sa.Text()))


def downgrade():
    with op.batch_alter_table("forest_trees") as batch:
        batch.drop_column("outline")
