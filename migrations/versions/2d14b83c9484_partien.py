"""Partien (Blöcke wie an der Tafel), Saatgut-Stufen, Reihen mit Zahlen

Revision ID: 2d14b83c9484
Revises: 05e9a853a1f0
Create Date: 2026-10-22 12:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = '2d14b83c9484'
down_revision = '05e9a853a1f0'
branch_labels = None
depends_on = None

DEFAULT_SEED_CLASSES = [
    ("PB", "Vorstufenpflanzgut"), ("S", ""), ("SE", ""), ("E", ""), ("BE", ""), ("Z", "Zertifiziert"),
]


def upgrade():
    seed_classes = op.create_table('seed_classes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=20), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.bulk_insert(seed_classes, [
        {"name": name, "description": desc or None, "position": i, "active": True}
        for i, (name, desc) in enumerate(DEFAULT_SEED_CLASSES, start=1)
    ])

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
        batch_op.add_column(sa.Column('lot_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('usage', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('seed_class_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('quantity_delta', sa.Numeric(precision=9, scale=2), nullable=True))
        batch_op.add_column(sa.Column('quantity_after', sa.Numeric(precision=9, scale=2), nullable=True))
        batch_op.alter_column('box_id', existing_type=sa.INTEGER(), nullable=True)
        batch_op.create_index(batch_op.f('ix_movements_lot_id'), ['lot_id'], unique=False)
        batch_op.create_foreign_key('fk_movements_lot_id_lots', 'lots', ['lot_id'], ['id'])
        batch_op.create_foreign_key('fk_movements_seed_class_id_seed_classes', 'seed_classes', ['seed_class_id'], ['id'])

    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('numeric_rows', sa.Boolean(), server_default=sa.false(), nullable=False))

    # Die Tafel ist ab jetzt die Startansicht der Lagerkarte
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('map_view', existing_type=sa.String(length=10), server_default='tafel')
    op.execute("UPDATE users SET map_view = 'tafel'")


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('map_view', existing_type=sa.String(length=10), server_default='karte')
    op.execute("UPDATE users SET map_view = 'karte' WHERE map_view = 'tafel'")
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.drop_column('numeric_rows')
    op.execute("DELETE FROM movements WHERE box_id IS NULL")
    with op.batch_alter_table('movements', schema=None) as batch_op:
        batch_op.drop_constraint('fk_movements_seed_class_id_seed_classes', type_='foreignkey')
        batch_op.drop_constraint('fk_movements_lot_id_lots', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_movements_lot_id'))
        batch_op.alter_column('box_id', existing_type=sa.INTEGER(), nullable=False)
        batch_op.drop_column('quantity_after')
        batch_op.drop_column('quantity_delta')
        batch_op.drop_column('seed_class_id')
        batch_op.drop_column('usage')
        batch_op.drop_column('lot_id')
    with op.batch_alter_table('lots', schema=None) as batch_op:
        batch_op.drop_index('ix_lot_place')
    op.drop_table('lots')
    op.drop_table('seed_classes')
