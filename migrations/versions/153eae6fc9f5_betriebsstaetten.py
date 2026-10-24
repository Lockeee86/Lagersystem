"""betriebsstaetten

Revision ID: 153eae6fc9f5
Revises: 208e491b6891
Create Date: 2026-10-24 05:55:00.052928

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '153eae6fc9f5'
down_revision = '208e491b6891'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sites',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('email', sa.String(length=120), nullable=True),
        sa.Column('passport_reg', sa.String(length=40), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_sites'),
        sa.UniqueConstraint('name', name='uq_sites_name'),
    )
    with op.batch_alter_table('plots', schema=None) as batch_op:
        batch_op.add_column(sa.Column('site_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_plots_site_id'), ['site_id'], unique=False)
        batch_op.create_foreign_key('fk_plots_site_id', 'sites', ['site_id'], ['id'])
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('site_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_warehouses_site_id'), ['site_id'], unique=False)
        batch_op.create_foreign_key('fk_warehouses_site_id', 'sites', ['site_id'], ['id'])
    with op.batch_alter_table('bulk_stores', schema=None) as batch_op:
        batch_op.add_column(sa.Column('site_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_bulk_stores_site_id'), ['site_id'], unique=False)
        batch_op.create_foreign_key('fk_bulk_stores_site_id', 'sites', ['site_id'], ['id'])
    with op.batch_alter_table('machines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('site_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_machines_site_id'), ['site_id'], unique=False)
        batch_op.create_foreign_key('fk_machines_site_id', 'sites', ['site_id'], ['id'])
    with op.batch_alter_table('deliveries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('site_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_deliveries_site_id'), ['site_id'], unique=False)
        batch_op.create_foreign_key('fk_deliveries_site_id', 'sites', ['site_id'], ['id'])


def downgrade():
    with op.batch_alter_table('deliveries', schema=None) as batch_op:
        batch_op.drop_constraint('fk_deliveries_site_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_deliveries_site_id'))
        batch_op.drop_column('site_id')
    with op.batch_alter_table('machines', schema=None) as batch_op:
        batch_op.drop_constraint('fk_machines_site_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_machines_site_id'))
        batch_op.drop_column('site_id')
    with op.batch_alter_table('bulk_stores', schema=None) as batch_op:
        batch_op.drop_constraint('fk_bulk_stores_site_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_bulk_stores_site_id'))
        batch_op.drop_column('site_id')
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.drop_constraint('fk_warehouses_site_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_warehouses_site_id'))
        batch_op.drop_column('site_id')
    with op.batch_alter_table('plots', schema=None) as batch_op:
        batch_op.drop_constraint('fk_plots_site_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_plots_site_id'))
        batch_op.drop_column('site_id')
    op.drop_table('sites')
