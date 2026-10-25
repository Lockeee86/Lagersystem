"""aufgabe_mehrere_schlaege

Revision ID: 3b7d2e9f41c6
Revises: aa9e99e22d0e
Create Date: 2026-10-25 08:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3b7d2e9f41c6'
down_revision = 'aa9e99e22d0e'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('task_plots',
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('plot_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['plot_id'], ['plots.id'], name='fk_task_plots_plot_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name='fk_task_plots_task_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('task_id', 'plot_id')
    )
    with op.batch_alter_table('task_plots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_plots_plot_id'), ['plot_id'], unique=False)
    # Bisherigen Schlag je Aufgabe übernehmen
    op.execute("INSERT INTO task_plots (task_id, plot_id) SELECT id, plot_id FROM tasks WHERE plot_id IS NOT NULL")
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint('fk_tasks_plot_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_tasks_plot_id'))
        batch_op.drop_column('plot_id')


def downgrade():
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plot_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_tasks_plot_id'), ['plot_id'], unique=False)
        batch_op.create_foreign_key('fk_tasks_plot_id', 'plots', ['plot_id'], ['id'], ondelete='SET NULL')
    # Bei mehreren Schlägen bleibt der erste (kleinste ID)
    op.execute("UPDATE tasks SET plot_id = (SELECT MIN(plot_id) FROM task_plots WHERE task_plots.task_id = tasks.id)")
    with op.batch_alter_table('task_plots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_plots_plot_id'))
    op.drop_table('task_plots')
