"""aufgabenpool: kurz notierte Arbeit, später in den Tagesplan einplanen

Revision ID: a8c2e6f4b913
Revises: f2b6d8a4c173
Create Date: 2026-11-01 15:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8c2e6f4b913'
down_revision = 'f2b6d8a4c173'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pool_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('text', sa.String(length=200), nullable=False),
        sa.Column('machine_id', sa.Integer(), nullable=True),
        sa.Column('important', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('done_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['machine_id'], ['machines.id'], name='fk_pool_items_machine_id_machines', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_pool_items_created_by_id_users', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['done_by_id'], ['users.id'], name='fk_pool_items_done_by_id_users', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name='pk_pool_items'),
    )
    op.create_index('ix_pool_items_done_at', 'pool_items', ['done_at'])
    with op.batch_alter_table('plan_entries') as batch_op:
        batch_op.add_column(sa.Column('pool_item_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_plan_entries_pool_item_id_pool_items', 'pool_items', ['pool_item_id'], ['id'],
                                    ondelete='SET NULL')
        batch_op.create_index('ix_plan_entries_pool_item_id', ['pool_item_id'])


def downgrade():
    with op.batch_alter_table('plan_entries') as batch_op:
        batch_op.drop_index('ix_plan_entries_pool_item_id')
        batch_op.drop_constraint('fk_plan_entries_pool_item_id_pool_items', type_='foreignkey')
        batch_op.drop_column('pool_item_id')
    op.drop_index('ix_pool_items_done_at', table_name='pool_items')
    op.drop_table('pool_items')
