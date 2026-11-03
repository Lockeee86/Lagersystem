"""biogas auch für admins abwählbar: bisherige admins behalten die rubrik

Bis jetzt sahen Admins Biogas immer, egal was im Haken stand. Damit sich beim Update nichts ändert, wird der Haken
bei allen Admins gesetzt; danach lässt er sich wie bei Mitarbeitern abwählen.

Revision ID: e3a7c1f5b429
Revises: d2f6b0e4a318
Create Date: 2026-11-03 12:00:00

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e3a7c1f5b429'
down_revision = 'd2f6b0e4a318'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET biogas = true WHERE role = 'admin'")


def downgrade():
    pass
