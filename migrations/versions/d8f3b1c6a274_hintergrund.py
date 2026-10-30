"""hintergrund: Glas-Design je Benutzer (Hintergrund und Farbstärke)

Revision ID: d8f3b1c6a274
Revises: c6e2a9d4b187
Create Date: 2026-10-30 16:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd8f3b1c6a274'
down_revision = 'c6e2a9d4b187'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('background', sa.String(length=20), server_default='sonnenaufgang', nullable=False))
        batch_op.add_column(sa.Column('bg_strength', sa.Integer(), server_default='80', nullable=False))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('bg_strength')
        batch_op.drop_column('background')
