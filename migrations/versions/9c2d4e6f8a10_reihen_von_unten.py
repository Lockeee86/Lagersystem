"""Lagerkarte: erste Reihe unten anzeigen

Revision ID: 9c2d4e6f8a10
Revises: 7b3e5c1d9a20
Create Date: 2026-10-22 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c2d4e6f8a10'
down_revision = '7b3e5c1d9a20'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rows_bottom_up', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.drop_column('rows_bottom_up')
