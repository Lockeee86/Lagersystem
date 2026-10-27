"""stall co2: CO₂-Wert vom Stall-Sensor (SCD41)

Revision ID: b4d1f7a2c956
Revises: a2c9e5f1b843
Create Date: 2026-10-27 18:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4d1f7a2c956'
down_revision = 'a2c9e5f1b843'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('coop_readings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('co2', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('coop_readings', schema=None) as batch_op:
        batch_op.drop_column('co2')
