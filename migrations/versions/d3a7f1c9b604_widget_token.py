"""Benutzer: Schlüssel für das iPhone-Widget

Revision ID: d3a7f1c9b604
Revises: c9f1d3b7e452
"""
import sqlalchemy as sa
from alembic import op

revision = "d3a7f1c9b604"
down_revision = "c9f1d3b7e452"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("widget_token", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_users_widget_token", ["widget_token"])


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_widget_token", type_="unique")
        batch.drop_column("widget_token")
