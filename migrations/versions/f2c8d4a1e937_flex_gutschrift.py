"""Gutschrift des Flex-Vermarkters je Einspeisestelle und Monat

Revision ID: f2c8d4a1e937
Revises: e5a1c7f3b282
"""
import sqlalchemy as sa
from alembic import op

revision = "f2c8d4a1e937"
down_revision = "e5a1c7f3b282"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "flex_statements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_point_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("kwh", sa.Numeric(12, 1), nullable=False),
        sa.Column("spot_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("market_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("intraday_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("fee_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("share_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("bonus_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["feed_point_id"], ["feed_points.id"], name="fk_flex_statements_feed_point_id_feed_points",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_flex_statements"),
        sa.UniqueConstraint("feed_point_id", "year", "month", name="uq_flex_statement"),
    )
    op.create_index("ix_flex_statements_feed_point_id", "flex_statements", ["feed_point_id"])


def downgrade():
    op.drop_index("ix_flex_statements_feed_point_id", table_name="flex_statements")
    op.drop_table("flex_statements")
