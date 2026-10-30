"""jahresbudget: Einspeisestellen mit Höchstbemessungsleistung, echte Monatsmengen

Revision ID: e2a7c4f9d813
Revises: d8f3b1c6a274
Create Date: 2026-10-30 22:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2a7c4f9d813'
down_revision = 'd8f3b1c6a274'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feed_points',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('max_kw', sa.Numeric(10, 1), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_feed_points'),
    )
    op.create_table(
        'feed_months',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('feed_point_id', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('kwh', sa.Numeric(12, 1), nullable=False),
        sa.ForeignKeyConstraint(['feed_point_id'], ['feed_points.id'], name='fk_feed_months_feed_point_id_feed_points',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name='pk_feed_months'),
        sa.UniqueConstraint('feed_point_id', 'year', 'month', name='uq_feed_month'),
    )
    op.create_index('ix_feed_months_feed_point_id', 'feed_months', ['feed_point_id'])
    with op.batch_alter_table('machines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('feed_point_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_machines_feed_point_id', ['feed_point_id'])
        batch_op.create_foreign_key('fk_machines_feed_point_id_feed_points', 'feed_points', ['feed_point_id'], ['id'],
                                    ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('machines', schema=None) as batch_op:
        batch_op.drop_constraint('fk_machines_feed_point_id_feed_points', type_='foreignkey')
        batch_op.drop_index('ix_machines_feed_point_id')
        batch_op.drop_column('feed_point_id')
    op.drop_index('ix_feed_months_feed_point_id', table_name='feed_months')
    op.drop_table('feed_months')
    op.drop_table('feed_points')
