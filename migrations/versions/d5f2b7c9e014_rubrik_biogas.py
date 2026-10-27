"""rubrik biogas je Benutzer abwählbar

Revision ID: d5f2b7c9e014
Revises: c3e8a1d4f702
Create Date: 2026-10-27 08:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5f2b7c9e014'
down_revision = 'c3e8a1d4f702'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('biogas', sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('biogas')
