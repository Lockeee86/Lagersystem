"""klappe_oben

Revision ID: cb9107ed7a26
Revises: 19feadb24625
Create Date: 2026-10-25 18:23:41.985437

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cb9107ed7a26'
down_revision = '19feadb24625'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('coop_door_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('position', sa.String(length=10), server_default='unten', nullable=False))



def downgrade():
    with op.batch_alter_table('coop_door_events', schema=None) as batch_op:
        batch_op.drop_column('position')

