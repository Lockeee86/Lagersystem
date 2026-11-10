"""Benutzer: Akzentfarbe und Menü am PC (Leiste links/oben)

Revision ID: b6e2f8a1d473
Revises: a4d7e1c9b352
"""
from alembic import op
import sqlalchemy as sa

revision = "b6e2f8a1d473"
down_revision = "a4d7e1c9b352"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("accent", sa.String(12), nullable=False, server_default=""))
        batch.add_column(sa.Column("nav_layout", sa.String(8), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("nav_layout")
        batch.drop_column("accent")
