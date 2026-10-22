"""Momentaufnahme des Kisteninhalts bei jeder Bewegung

Damit lassen sich frühere Kartoffeljahre auswerten, auch wenn eine Kiste später
neu befüllt wird. Bestehende Bewegungen erhalten den aktuellen Inhalt ihrer Kiste
(beste verfügbare Annäherung).

Revision ID: 05e9a853a1f0
Revises: 586c0953e00e
Create Date: 2026-10-22 09:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = '05e9a853a1f0'
down_revision = '586c0953e00e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('movements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('variety_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('plot_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('harvest_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('fill', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_movements_harvest_date'), ['harvest_date'], unique=False)
        batch_op.create_foreign_key('fk_movements_variety_id_varieties', 'varieties', ['variety_id'], ['id'])
        batch_op.create_foreign_key('fk_movements_plot_id_plots', 'plots', ['plot_id'], ['id'])

    op.execute("""
        UPDATE movements SET
            variety_id = (SELECT b.variety_id FROM boxes b WHERE b.id = movements.box_id),
            plot_id = (SELECT b.plot_id FROM boxes b WHERE b.id = movements.box_id),
            harvest_date = (SELECT b.harvest_date FROM boxes b WHERE b.id = movements.box_id),
            fill = COALESCE(movements.fill_after, (SELECT b.fill FROM boxes b WHERE b.id = movements.box_id))
        WHERE movements.action <> 'angelegt'
    """)


def downgrade():
    with op.batch_alter_table('movements', schema=None) as batch_op:
        batch_op.drop_constraint('fk_movements_plot_id_plots', type_='foreignkey')
        batch_op.drop_constraint('fk_movements_variety_id_varieties', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_movements_harvest_date'))
        batch_op.drop_column('fill')
        batch_op.drop_column('harvest_date')
        batch_op.drop_column('plot_id')
        batch_op.drop_column('variety_id')
