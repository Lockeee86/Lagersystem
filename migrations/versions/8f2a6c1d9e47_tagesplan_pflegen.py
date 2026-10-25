"""tagesplan_pflegen

Revision ID: 8f2a6c1d9e47
Revises: 5e1c8a7d2b90
Create Date: 2026-10-25 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f2a6c1d9e47'
down_revision = '5e1c8a7d2b90'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('can_plan', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('can_plan')
