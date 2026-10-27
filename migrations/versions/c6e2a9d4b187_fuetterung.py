"""fütterung: was es täglich gab (Weizen, Legemehl)

Revision ID: c6e2a9d4b187
Revises: b4d1f7a2c956
Create Date: 2026-10-28 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6e2a9d4b187'
down_revision = 'b4d1f7a2c956'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feedings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('wheat', sa.Boolean(), nullable=False),
        sa.Column('mash', sa.Boolean(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_feedings_user_id_users', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name='pk_feedings'),
        sa.UniqueConstraint('day', name='uq_feedings_day'),
    )


def downgrade():
    op.drop_table('feedings')
