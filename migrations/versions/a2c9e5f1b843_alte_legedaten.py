"""alte legedaten: importierte Eier-Einträge (zählen nicht zum Vorrat)

Revision ID: a2c9e5f1b843
Revises: f1b4d8e2a637
Create Date: 2026-10-27 15:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2c9e5f1b843'
down_revision = 'f1b4d8e2a637'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('egg_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('imported', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    with op.batch_alter_table('egg_entries', schema=None) as batch_op:
        batch_op.drop_column('imported')
