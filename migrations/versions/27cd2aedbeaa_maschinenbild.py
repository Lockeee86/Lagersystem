"""maschinenbild

Revision ID: 27cd2aedbeaa
Revises: fde71ec23094
Create Date: 2026-10-24 10:22:13.369740

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '27cd2aedbeaa'
down_revision = 'fde71ec23094'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('machines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('image', sa.String(length=80), nullable=True))



def downgrade():
    with op.batch_alter_table('machines', schema=None) as batch_op:
        batch_op.drop_column('image')

