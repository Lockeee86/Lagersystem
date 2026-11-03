"""mehrstrom-rechner: anzulegende werte, flexprämie und optional substrat je einspeisestelle

Revision ID: f4b8d2a6c530
Revises: e3a7c1f5b429
Create Date: 2026-11-03 15:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f4b8d2a6c530'
down_revision = 'e3a7c1f5b429'
branch_labels = None
depends_on = None

COLUMNS = (("aw_stage1", sa.Numeric(7, 3)), ("aw_stage2", sa.Numeric(7, 3)), ("aw_stage3", sa.Numeric(7, 3)),
           ("flex_eur_kw", sa.Numeric(8, 2)), ("flex_factor", sa.Numeric(4, 2)),
           ("substrate_eur_t", sa.Numeric(8, 2)), ("substrate_kwh_t", sa.Numeric(8, 1)))


def upgrade():
    with op.batch_alter_table('feed_points') as batch_op:
        for name, kind in COLUMNS:
            batch_op.add_column(sa.Column(name, kind, nullable=True))


def downgrade():
    with op.batch_alter_table('feed_points') as batch_op:
        for name, _kind in reversed(COLUMNS):
            batch_op.drop_column(name)
