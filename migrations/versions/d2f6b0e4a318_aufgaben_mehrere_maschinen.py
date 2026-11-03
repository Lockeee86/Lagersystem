"""aufgaben: mehrere maschinen je aufgabe (task_machines); bisherige maschine wird übernommen

Revision ID: d2f6b0e4a318
Revises: c1e5a9d3f207
Create Date: 2026-11-03 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2f6b0e4a318'
down_revision = 'c1e5a9d3f207'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'task_machines',
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('machine_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name='fk_task_machines_task_id_tasks', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['machine_id'], ['machines.id'], name='fk_task_machines_machine_id_machines',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('task_id', 'machine_id', name='pk_task_machines'),
    )
    op.create_index('ix_task_machines_machine_id', 'task_machines', ['machine_id'])
    op.execute("INSERT INTO task_machines (task_id, machine_id) SELECT id, machine_id FROM tasks WHERE machine_id IS NOT NULL")


def downgrade():
    op.drop_index('ix_task_machines_machine_id', table_name='task_machines')
    op.drop_table('task_machines')
