"""Gutschrift: Belegnummer und Storno (mehrere Belege je Monat)

Revision ID: a7d3e9b5c218
Revises: f2c8d4a1e937
"""
import sqlalchemy as sa
from alembic import op

revision = "a7d3e9b5c218"
down_revision = "f2c8d4a1e937"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("flex_statements") as batch:
        batch.add_column(sa.Column("number", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("cancels", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("storno", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.drop_constraint("uq_flex_statement", type_="unique")
        batch.create_unique_constraint("uq_flex_statement_number", ["feed_point_id", "number"])


def downgrade():
    op.execute("DELETE FROM flex_statements WHERE storno")
    op.execute("DELETE FROM flex_statements a USING flex_statements b "
               "WHERE a.feed_point_id = b.feed_point_id AND a.year = b.year AND a.month = b.month AND a.id < b.id")
    with op.batch_alter_table("flex_statements") as batch:
        batch.drop_constraint("uq_flex_statement_number", type_="unique")
        batch.create_unique_constraint("uq_flex_statement", ["feed_point_id", "year", "month"])
        batch.drop_column("storno")
        batch.drop_column("cancels")
        batch.drop_column("number")
