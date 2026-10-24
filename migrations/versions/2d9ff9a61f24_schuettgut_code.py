"""schuettgut_code

Revision ID: 2d9ff9a61f24
Revises: 2c03f3170a8a
Create Date: 2026-10-24 05:30:31.899038

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2d9ff9a61f24'
down_revision = '2c03f3170a8a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bulk_stores', schema=None) as batch_op:
        batch_op.add_column(sa.Column('code', sa.String(length=10), nullable=True))
        batch_op.create_unique_constraint('uq_bulk_stores_code', ['code'])
    # bestehende Lagerorte bekommen der Reihe nach S-001, S-002 …
    conn = op.get_bind()
    ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM bulk_stores ORDER BY id"))]
    for n, store_id in enumerate(ids, start=1):
        conn.execute(sa.text("UPDATE bulk_stores SET code = :code WHERE id = :id"), {"code": f"S-{n:03d}", "id": store_id})


def downgrade():
    with op.batch_alter_table('bulk_stores', schema=None) as batch_op:
        batch_op.drop_constraint('uq_bulk_stores_code', type_='unique')
        batch_op.drop_column('code')
