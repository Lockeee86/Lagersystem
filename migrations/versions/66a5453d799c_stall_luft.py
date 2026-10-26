"""stall_luft

Revision ID: 66a5453d799c
Revises: cb9107ed7a26
Create Date: 2026-10-26 10:10:15.606867

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '66a5453d799c'
down_revision = 'cb9107ed7a26'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('coop_readings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pressure', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('gas', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('iaq', sa.Integer(), nullable=True))



def downgrade():
    with op.batch_alter_table('coop_readings', schema=None) as batch_op:
        batch_op.drop_column('iaq')
        batch_op.drop_column('gas')
        batch_op.drop_column('pressure')

