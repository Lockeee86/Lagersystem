"""tagesplan: Einträge aus Vorschlägen (Aufgabe, Wartung, Störung)

Revision ID: d5f1b9c3e842
Revises: c3e8a1f6d927
Create Date: 2026-10-31 20:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5f1b9c3e842'
down_revision = 'c3e8a1f6d927'
branch_labels = None
depends_on = None

LINKS = (('task_id', 'tasks'), ('maintenance_task_id', 'maintenance_tasks'), ('machine_event_id', 'machine_events'))


def upgrade():
    with op.batch_alter_table('plan_entries') as batch_op:
        for column, table in LINKS:
            batch_op.add_column(sa.Column(column, sa.Integer(), nullable=True))
            batch_op.create_foreign_key(f'fk_plan_entries_{column}_{table}', table, [column], ['id'], ondelete='SET NULL')
            batch_op.create_index(f'ix_plan_entries_{column}', [column])


def downgrade():
    with op.batch_alter_table('plan_entries') as batch_op:
        for column, table in LINKS:
            batch_op.drop_index(f'ix_plan_entries_{column}')
            batch_op.drop_constraint(f'fk_plan_entries_{column}_{table}', type_='foreignkey')
            batch_op.drop_column(column)
