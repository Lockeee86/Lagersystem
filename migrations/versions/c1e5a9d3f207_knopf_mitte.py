"""knopf in der mitte der unteren leiste: einlagern (standard) oder scannen

Revision ID: c1e5a9d3f207
Revises: b9d3f7a5c024
Create Date: 2026-11-02 14:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1e5a9d3f207'
down_revision = 'b9d3f7a5c024'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('tab_center', sa.String(length=12), nullable=False, server_default=''))


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('tab_center')
