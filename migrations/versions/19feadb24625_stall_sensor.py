"""stall_sensor

Revision ID: 19feadb24625
Revises: ff66b5f1e628
Create Date: 2026-10-25 15:45:17.360172

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '19feadb24625'
down_revision = 'ff66b5f1e628'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('coop_door_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_open', sa.Boolean(), nullable=False),
    sa.Column('node', sa.String(length=20), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('coop_door_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_coop_door_events_at'), ['at'], unique=False)

    op.create_table('coop_readings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('node', sa.String(length=20), nullable=True),
    sa.Column('temperature', sa.Float(), nullable=True),
    sa.Column('humidity', sa.Float(), nullable=True),
    sa.Column('battery', sa.Float(), nullable=True),
    sa.Column('voltage', sa.Float(), nullable=True),
    sa.Column('rssi', sa.Float(), nullable=True),
    sa.Column('snr', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('coop_readings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_coop_readings_at'), ['at'], unique=False)



def downgrade():
    with op.batch_alter_table('coop_readings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_coop_readings_at'))

    op.drop_table('coop_readings')
    with op.batch_alter_table('coop_door_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_coop_door_events_at'))

    op.drop_table('coop_door_events')
