"""Einstellungen des Betriebs (Hofname, Anschrift)

Revision ID: a1b3c5d7e9f2
Revises: 9c2d4e6f8a10
Create Date: 2026-10-22 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b3c5d7e9f2'
down_revision = '9c2d4e6f8a10'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'settings',
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade():
    op.drop_table('settings')
