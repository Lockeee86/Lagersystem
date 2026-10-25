"""tagesplan_mehrere_schlaege

Revision ID: b4d9e3f6a215
Revises: 8f2a6c1d9e47
Create Date: 2026-10-25 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4d9e3f6a215'
down_revision = '8f2a6c1d9e47'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('plan_entry_plots',
        sa.Column('entry_id', sa.Integer(), nullable=False),
        sa.Column('plot_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['entry_id'], ['plan_entries.id'], name='fk_plan_entry_plots_entry_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plot_id'], ['plots.id'], name='fk_plan_entry_plots_plot_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('entry_id', 'plot_id')
    )
    with op.batch_alter_table('plan_entry_plots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_plan_entry_plots_plot_id'), ['plot_id'], unique=False)
    # Bisherigen Schlag je Eintrag übernehmen
    op.execute("INSERT INTO plan_entry_plots (entry_id, plot_id) "
               "SELECT id, plot_id FROM plan_entries WHERE plot_id IS NOT NULL")
    with op.batch_alter_table('plan_entries', schema=None) as batch_op:
        batch_op.drop_constraint('fk_plan_entries_plot_id', type_='foreignkey')
        batch_op.drop_column('plot_id')


def downgrade():
    with op.batch_alter_table('plan_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plot_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_plan_entries_plot_id', 'plots', ['plot_id'], ['id'], ondelete='SET NULL')
    # Bei mehreren Schlägen bleibt der erste (kleinste ID)
    op.execute("UPDATE plan_entries SET plot_id = (SELECT MIN(plot_id) FROM plan_entry_plots "
               "WHERE plan_entry_plots.entry_id = plan_entries.id)")
    with op.batch_alter_table('plan_entry_plots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_plan_entry_plots_plot_id'))
    op.drop_table('plan_entry_plots')
