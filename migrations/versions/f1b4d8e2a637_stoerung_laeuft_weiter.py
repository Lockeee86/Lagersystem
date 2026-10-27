"""störung: Maschine kann weiterlaufen

Revision ID: f1b4d8e2a637
Revises: e8a3c6d1f925
Create Date: 2026-10-27 12:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1b4d8e2a637'
down_revision = 'e8a3c6d1f925'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('machine_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('still_running', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    with op.batch_alter_table('machine_events', schema=None) as batch_op:
        batch_op.drop_column('still_running')
