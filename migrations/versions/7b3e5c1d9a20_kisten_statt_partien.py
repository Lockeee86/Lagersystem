"""Partien wieder entfernt – Einzelkisten mit Verwendung und Stufe

Die Tafel-/Partien-Arbeitsweise wird nicht genutzt. Saatgut-Stufen und Reihen mit Zahlen
bleiben; Kisten bekommen Verwendung (Saat/Speise) und Stufe.
Achtung: vorhandene Partien und ihr Verlauf werden gelöscht.

Revision ID: 7b3e5c1d9a20
Revises: 2d14b83c9484
Create Date: 2026-10-22 15:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = '7b3e5c1d9a20'
down_revision = '2d14b83c9484'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM movements WHERE box_id IS NULL")
    with op.batch_alter_table('movements', schema=None) as batch_op:
        batch_op.drop_constraint('fk_movements_lot_id_lots', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_movements_lot_id'))
        batch_op.drop_column('lot_id')
        batch_op.drop_column('quantity_delta')
        batch_op.drop_column('quantity_after')
        batch_op.alter_column('box_id', existing_type=sa.INTEGER(), nullable=False)
    with op.batch_alter_table('lots', schema=None) as batch_op:
        batch_op.drop_index('ix_lot_place')
    op.drop_table('lots')

    with op.batch_alter_table('boxes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('usage', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('seed_class_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_boxes_seed_class_id_seed_classes', 'seed_classes', ['seed_class_id'], ['id'])

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('map_view', existing_type=sa.String(length=10), server_default='karte')
    op.execute("UPDATE users SET map_view = 'karte' WHERE map_view = 'tafel'")


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('map_view', existing_type=sa.String(length=10), server_default='tafel')
    with op.batch_alter_table('boxes', schema=None) as batch_op:
        batch_op.drop_constraint('fk_boxes_seed_class_id_seed_classes', type_='foreignkey')
        batch_op.drop_column('seed_class_id')
        batch_op.drop_column('usage')
    op.create_table('lots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('warehouse_id', sa.Integer(), nullable=False),
    sa.Column('row', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('variety_id', sa.Integer(), nullable=False),
    sa.Column('usage', sa.String(length=20), nullable=False),
    sa.Column('seed_class_id', sa.Integer(), nullable=True),
    sa.Column('grade', sa.String(length=50), nullable=True),
    sa.Column('plot_id', sa.Integer(), nullable=True),
    sa.Column('harvest_date', sa.Date(), nullable=True),
    sa.Column('quantity', sa.Numeric(precision=9, scale=2), nullable=False),
    sa.Column('box_type_id', sa.Integer(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('closed', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['box_type_id'], ['box_types.id'], name='fk_lots_box_type_id_box_types'),
    sa.ForeignKeyConstraint(['plot_id'], ['plots.id'], name='fk_lots_plot_id_plots'),
    sa.ForeignKeyConstraint(['seed_class_id'], ['seed_classes.id'], name='fk_lots_seed_class_id_seed_classes'),
    sa.ForeignKeyConstraint(['variety_id'], ['varieties.id'], name='fk_lots_variety_id_varieties'),
    sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], name='fk_lots_warehouse_id_warehouses'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('lots', schema=None) as batch_op:
        batch_op.create_index('ix_lot_place', ['warehouse_id', 'row', 'position'], unique=False)
    with op.batch_alter_table('movements', schema=None) as batch_op:
        batch_op.alter_column('box_id', existing_type=sa.INTEGER(), nullable=True)
        batch_op.add_column(sa.Column('quantity_after', sa.Numeric(precision=9, scale=2), nullable=True))
        batch_op.add_column(sa.Column('quantity_delta', sa.Numeric(precision=9, scale=2), nullable=True))
        batch_op.add_column(sa.Column('lot_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_movements_lot_id'), ['lot_id'], unique=False)
        batch_op.create_foreign_key('fk_movements_lot_id_lots', 'lots', ['lot_id'], ['id'])
