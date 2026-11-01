"""klassisches Design als Standard für alle, Hell/Dunkel nach Tageszeit

Revision ID: f2b6d8a4c173
Revises: e7a3c5d1f960
Create Date: 2026-11-01 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2b6d8a4c173'
down_revision = 'e7a3c5d1f960'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('theme_mode', sa.String(length=10), nullable=False, server_default='auto'))
        batch_op.alter_column('background', existing_type=sa.String(length=20), existing_nullable=False,
                              server_default='klassisch')
    # auf Wunsch: alle Benutzer auf das klassische Design (jeder kann danach wieder ein Glas-Design wählen)
    op.execute("UPDATE users SET background = 'klassisch'")


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column('background', existing_type=sa.String(length=20), existing_nullable=False,
                              server_default='sonnenaufgang')
        batch_op.drop_column('theme_mode')
