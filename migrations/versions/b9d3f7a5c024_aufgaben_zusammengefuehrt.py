"""aufgaben zusammengeführt: Tagesplan-Einträge und Aufgabenpool werden Aufgaben mit geplantem Tag

Eine Aufgabe ist jetzt alles, was zu tun ist. Eingeplant = `tasks.plan_day` + eine Person (`task_assignees`).
- Pool-Einträge → Aufgaben ohne Person und Tag (erledigte bleiben erledigt).
- Tagesplan-Einträge → Aufgaben mit Tag und Person. War ein Eintrag mit einer Aufgabe bzw. einem Pool-Eintrag
  verknüpft, bekommt diese Aufgabe den Tag und die Person (der jüngste Eintrag zählt), statt eine neue entsteht.
- Verknüpfungen zu Wartung/Störung und Schläge werden mitgenommen.

Revision ID: b9d3f7a5c024
Revises: a8c2e6f4b913
Create Date: 2026-11-02 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9d3f7a5c024'
down_revision = 'a8c2e6f4b913'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.add_column(sa.Column('plan_day', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('plan_position', sa.Integer(), nullable=False, server_default='0'))
        batch_op.create_index('ix_tasks_plan_day', ['plan_day'])

    conn = op.get_bind()
    meta = sa.MetaData()
    tasks = sa.Table('tasks', meta, autoload_with=conn)
    assignees = sa.Table('task_assignees', meta, autoload_with=conn)
    task_plots = sa.Table('task_plots', meta, autoload_with=conn)
    pool = sa.Table('pool_items', meta, autoload_with=conn)
    entries = sa.Table('plan_entries', meta, autoload_with=conn)
    entry_plots = sa.Table('plan_entry_plots', meta, autoload_with=conn)

    def new_task(**values):
        return conn.execute(sa.insert(tasks).values(**values).returning(tasks.c.id)).scalar_one()

    # Pool → Aufgaben
    pool_task = {}
    for p in conn.execute(sa.select(pool)).mappings():
        pool_task[p['id']] = new_task(title=p['text'], important=p['important'], machine_id=p['machine_id'],
                                      created_by_id=p['created_by_id'], created_at=p['created_at'],
                                      done_at=p['done_at'], done_by_id=p['done_by_id'], plan_position=0)

    plots_of = {}
    for row in conn.execute(sa.select(entry_plots)).mappings():
        plots_of.setdefault(row['entry_id'], []).append(row['plot_id'])

    def set_plan(task_id, entry):
        conn.execute(sa.update(tasks).where(tasks.c.id == task_id)
                     .values(plan_day=entry['day'], plan_position=entry['position']))
        conn.execute(sa.delete(assignees).where(assignees.c.task_id == task_id))
        conn.execute(sa.insert(assignees).values(task_id=task_id, user_id=entry['user_id']))
        if entry['done_at'] is not None:
            conn.execute(sa.update(tasks).where(tasks.c.id == task_id, tasks.c.done_at.is_(None))
                         .values(done_at=entry['done_at'], done_by_id=entry['done_by_id']))

    # Tagesplan → Aufgaben (ältester Tag zuerst, damit der jüngste Eintrag gewinnt)
    for e in conn.execute(sa.select(entries).order_by(entries.c.day, entries.c.id)).mappings():
        target = e['task_id'] or pool_task.get(e['pool_item_id'])
        if target is not None:
            set_plan(target, e)
            continue
        task_id = new_task(title=e['text'], machine_id=e['machine_id'], plan_day=e['day'], plan_position=e['position'],
                           created_by_id=e['created_by_id'], created_at=e['created_at'], done_at=e['done_at'],
                           done_by_id=e['done_by_id'], maintenance_task_id=e['maintenance_task_id'],
                           machine_event_id=e['machine_event_id'], important=False)
        conn.execute(sa.insert(assignees).values(task_id=task_id, user_id=e['user_id']))
        for plot_id in plots_of.get(e['id'], []):
            conn.execute(sa.insert(task_plots).values(task_id=task_id, plot_id=plot_id))

    op.drop_table('plan_entry_plots')
    op.drop_table('plan_entries')
    op.drop_table('pool_items')


def downgrade():
    # Tabellen leer wiederherstellen; geplante Aufgaben bleiben Aufgaben (ohne Tag)
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
    op.create_table(
        'plan_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('text', sa.String(length=200), nullable=False),
        sa.Column('machine_id', sa.Integer(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('done_by_id', sa.Integer(), nullable=True),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('maintenance_task_id', sa.Integer(), nullable=True),
        sa.Column('machine_event_id', sa.Integer(), nullable=True),
        sa.Column('pool_item_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_plan_entries_created_by_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['machine_id'], ['machines.id'], name='fk_plan_entries_machine_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_plan_entries_user_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['done_by_id'], ['users.id'], name='fk_plan_entries_done_by_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name='fk_plan_entries_task_id_tasks', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['maintenance_task_id'], ['maintenance_tasks.id'],
                                name='fk_plan_entries_maintenance_task_id_maintenance_tasks', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['machine_event_id'], ['machine_events.id'],
                                name='fk_plan_entries_machine_event_id_machine_events', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['pool_item_id'], ['pool_items.id'], name='fk_plan_entries_pool_item_id_pool_items',
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in ('day', 'user_id', 'task_id', 'maintenance_task_id', 'machine_event_id', 'pool_item_id'):
        op.create_index(f'ix_plan_entries_{column}', 'plan_entries', [column])
    op.create_table(
        'plan_entry_plots',
        sa.Column('entry_id', sa.Integer(), nullable=False),
        sa.Column('plot_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['entry_id'], ['plan_entries.id'], name='fk_plan_entry_plots_entry_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plot_id'], ['plots.id'], name='fk_plan_entry_plots_plot_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('entry_id', 'plot_id'),
    )
    op.create_index('ix_plan_entry_plots_plot_id', 'plan_entry_plots', ['plot_id'])
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.drop_index('ix_tasks_plan_day')
        batch_op.drop_column('plan_position')
        batch_op.drop_column('plan_day')
