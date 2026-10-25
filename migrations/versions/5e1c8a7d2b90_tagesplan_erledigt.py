"""tagesplan_erledigt

Revision ID: 5e1c8a7d2b90
Revises: 3b7d2e9f41c6
Create Date: 2026-10-25 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5e1c8a7d2b90'
down_revision = '3b7d2e9f41c6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('plan_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('done_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('done_by_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_plan_entries_done_by_id', 'users', ['done_by_id'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('plan_entries', schema=None) as batch_op:
        batch_op.drop_constraint('fk_plan_entries_done_by_id', type_='foreignkey')
        batch_op.drop_column('done_by_id')
        batch_op.drop_column('done_at')
