"""technische plätze: Baum fester Orte, Maschinen dort eingebaut

Revision ID: e7a3c5d1f960
Revises: d5f1b9c3e842
Create Date: 2026-10-31 22:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7a3c5d1f960'
down_revision = 'd5f1b9c3e842'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tech_places',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('warehouse_id', sa.Integer(), nullable=True),
        sa.Column('biogas', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['parent_id'], ['tech_places.id'], name='fk_tech_places_parent_id_tech_places'),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], name='fk_tech_places_site_id_sites'),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], name='fk_tech_places_warehouse_id_warehouses',
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name='pk_tech_places'),
        sa.UniqueConstraint('code', name='uq_tech_places_code'),
    )
    for column in ('parent_id', 'site_id', 'warehouse_id'):
        op.create_index(f'ix_tech_places_{column}', 'tech_places', [column])
    with op.batch_alter_table('machines') as batch_op:
        batch_op.add_column(sa.Column('place_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_machines_place_id_tech_places', 'tech_places', ['place_id'], ['id'],
                                    ondelete='SET NULL')
        batch_op.create_index('ix_machines_place_id', ['place_id'])


def downgrade():
    with op.batch_alter_table('machines') as batch_op:
        batch_op.drop_index('ix_machines_place_id')
        batch_op.drop_constraint('fk_machines_place_id_tech_places', type_='foreignkey')
        batch_op.drop_column('place_id')
    for column in ('parent_id', 'site_id', 'warehouse_id'):
        op.drop_index(f'ix_tech_places_{column}', table_name='tech_places')
    op.drop_table('tech_places')
