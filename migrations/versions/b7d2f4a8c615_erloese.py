"""erloese: Beträge je Abrechnungs-Kategorie, Einspeisestelle und Monat

Revision ID: b7d2f4a8c615
Revises: a5c9e3f7b204
Create Date: 2026-10-31 15:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d2f4a8c615'
down_revision = 'a5c9e3f7b204'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feed_revenues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('feed_point_id', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=60), nullable=False),
        sa.Column('kwh', sa.Numeric(12, 1), nullable=False),
        sa.Column('eur', sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(['feed_point_id'], ['feed_points.id'], name='fk_feed_revenues_feed_point_id_feed_points',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name='pk_feed_revenues'),
        sa.UniqueConstraint('feed_point_id', 'year', 'month', 'category', name='uq_feed_revenue'),
    )
    op.create_index('ix_feed_revenues_feed_point_id', 'feed_revenues', ['feed_point_id'])


def downgrade():
    op.drop_index('ix_feed_revenues_feed_point_id', table_name='feed_revenues')
    op.drop_table('feed_revenues')
