"""jahresmenge kwh: Budget der Einspeisestelle auch als erlaubte Jahresmenge (kWh)

Revision ID: f4b8d2e6a391
Revises: e2a7c4f9d813
Create Date: 2026-10-31 10:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f4b8d2e6a391'
down_revision = 'e2a7c4f9d813'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('feed_points', schema=None) as batch_op:
        batch_op.add_column(sa.Column('annual_kwh', sa.Numeric(14, 1), nullable=True))
        batch_op.alter_column('max_kw', existing_type=sa.Numeric(10, 1), nullable=True)


def downgrade():
    op.execute("UPDATE feed_points SET max_kw = annual_kwh / 8760 WHERE max_kw IS NULL")
    with op.batch_alter_table('feed_points', schema=None) as batch_op:
        batch_op.alter_column('max_kw', existing_type=sa.Numeric(10, 1), nullable=False)
        batch_op.drop_column('annual_kwh')
