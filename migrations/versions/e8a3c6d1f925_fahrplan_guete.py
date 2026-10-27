"""fahrplan-güte: Vergleichserlös je BHKW-Tag

Revision ID: e8a3c6d1f925
Revises: d5f2b7c9e014
Create Date: 2026-10-27 10:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e8a3c6d1f925'
down_revision = 'd5f2b7c9e014'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('chp_days', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ideal_revenue_eur', sa.Numeric(precision=10, scale=2), nullable=True))


def downgrade():
    with op.batch_alter_table('chp_days', schema=None) as batch_op:
        batch_op.drop_column('ideal_revenue_eur')
