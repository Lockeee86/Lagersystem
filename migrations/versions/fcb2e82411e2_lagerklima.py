"""lagerklima

Revision ID: fcb2e82411e2
Revises: 66a5453d799c
Create Date: 2026-10-26 10:56:11.154116

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fcb2e82411e2'
down_revision = '66a5453d799c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('climate_sensors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('node', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=True),
    sa.Column('warehouse_id', sa.Integer(), nullable=True),
    sa.Column('row', sa.Integer(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], name='fk_climate_sensors_warehouse_id', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('node', name='uq_climate_sensors_node')
    )
    with op.batch_alter_table('climate_sensors', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_climate_sensors_warehouse_id'), ['warehouse_id'], unique=False)

    op.create_table('climate_readings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sensor_id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('temperature', sa.Float(), nullable=True),
    sa.Column('humidity', sa.Float(), nullable=True),
    sa.Column('co2', sa.Integer(), nullable=True),
    sa.Column('battery', sa.Float(), nullable=True),
    sa.Column('voltage', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['sensor_id'], ['climate_sensors.id'], name='fk_climate_readings_sensor_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('climate_readings', schema=None) as batch_op:
        batch_op.create_index('ix_climate_readings_sensor_at', ['sensor_id', 'at'], unique=False)

    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('climate_temp_min', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('climate_temp_max', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('climate_hum_min', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('climate_hum_max', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('climate_co2_max', sa.Integer(), nullable=True))



def downgrade():
    with op.batch_alter_table('warehouses', schema=None) as batch_op:
        batch_op.drop_column('climate_co2_max')
        batch_op.drop_column('climate_hum_max')
        batch_op.drop_column('climate_hum_min')
        batch_op.drop_column('climate_temp_max')
        batch_op.drop_column('climate_temp_min')

    with op.batch_alter_table('climate_readings', schema=None) as batch_op:
        batch_op.drop_index('ix_climate_readings_sensor_at')

    op.drop_table('climate_readings')
    with op.batch_alter_table('climate_sensors', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_climate_sensors_warehouse_id'))

    op.drop_table('climate_sensors')
