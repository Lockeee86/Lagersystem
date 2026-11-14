"""Erlöse: Quelle Netzbetreiber/Direktvermarkter und Eingabe von Hand

Revision ID: e5a1c7f3b282
Revises: d8b4e2a6f915
"""
import sqlalchemy as sa
from alembic import op

revision = "e5a1c7f3b282"
down_revision = "d8b4e2a6f915"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("feed_revenues") as batch:
        batch.add_column(sa.Column("source", sa.String(length=10), nullable=False, server_default="netz"))
        batch.add_column(sa.Column("manual", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.drop_constraint("uq_feed_revenue", type_="unique")
        batch.create_unique_constraint("uq_feed_revenue", ["feed_point_id", "year", "month", "category", "source"])


def downgrade():
    op.execute("DELETE FROM feed_revenues WHERE source <> 'netz'")
    with op.batch_alter_table("feed_revenues") as batch:
        batch.drop_constraint("uq_feed_revenue", type_="unique")
        batch.create_unique_constraint("uq_feed_revenue", ["feed_point_id", "year", "month", "category"])
        batch.drop_column("manual")
        batch.drop_column("source")
