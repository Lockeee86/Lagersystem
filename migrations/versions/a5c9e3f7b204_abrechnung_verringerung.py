"""abrechnung verringerung: kWh ohne Marktprämie (über der Höchstbemessungsleistung) je Monat

Revision ID: a5c9e3f7b204
Revises: f4b8d2e6a391
Create Date: 2026-10-31 12:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a5c9e3f7b204'
down_revision = 'f4b8d2e6a391'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('feed_months', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reduced_kwh', sa.Numeric(12, 1), nullable=True))


def downgrade():
    with op.batch_alter_table('feed_months', schema=None) as batch_op:
        batch_op.drop_column('reduced_kwh')
