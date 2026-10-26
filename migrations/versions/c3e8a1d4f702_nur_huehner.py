"""nur hühner: Benutzer, die ausschließlich die Hühner sehen

Revision ID: c3e8a1d4f702
Revises: a7c4e2f9b318
Create Date: 2026-10-26 21:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e8a1d4f702'
down_revision = 'a7c4e2f9b318'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('chickens_only', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('chickens_only')
