"""fotos_an_kisten_schlaegen

Revision ID: 208e491b6891
Revises: 2d9ff9a61f24
Create Date: 2026-10-24 05:50:16.043237

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '208e491b6891'
down_revision = '2d9ff9a61f24'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('photos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('box_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('plot_year_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('bulk_store_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('delivery_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_photos_box_id'), ['box_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_photos_plot_year_id'), ['plot_year_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_photos_bulk_store_id'), ['bulk_store_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_photos_delivery_id'), ['delivery_id'], unique=False)
        batch_op.create_foreign_key('fk_photos_box_id', 'boxes', ['box_id'], ['id'])
        batch_op.create_foreign_key('fk_photos_plot_year_id', 'plot_years', ['plot_year_id'], ['id'])
        batch_op.create_foreign_key('fk_photos_bulk_store_id', 'bulk_stores', ['bulk_store_id'], ['id'])
        batch_op.create_foreign_key('fk_photos_delivery_id', 'deliveries', ['delivery_id'], ['id'])


def downgrade():
    with op.batch_alter_table('photos', schema=None) as batch_op:
        batch_op.drop_constraint('fk_photos_box_id', type_='foreignkey')
        batch_op.drop_constraint('fk_photos_plot_year_id', type_='foreignkey')
        batch_op.drop_constraint('fk_photos_bulk_store_id', type_='foreignkey')
        batch_op.drop_constraint('fk_photos_delivery_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_photos_box_id'))
        batch_op.drop_index(batch_op.f('ix_photos_plot_year_id'))
        batch_op.drop_index(batch_op.f('ix_photos_bulk_store_id'))
        batch_op.drop_index(batch_op.f('ix_photos_delivery_id'))
        batch_op.drop_column('box_id')
        batch_op.drop_column('plot_year_id')
        batch_op.drop_column('bulk_store_id')
        batch_op.drop_column('delivery_id')
