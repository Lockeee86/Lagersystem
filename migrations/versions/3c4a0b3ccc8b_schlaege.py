"""Schläge als Stammdaten statt Freitext

Bisher stand der Schlag als Freitext in boxes.field. Jetzt gibt es die Tabelle
plots; aus jedem vorhandenen Freitext-Eintrag wird automatisch ein Schlag.

Revision ID: 3c4a0b3ccc8b
Revises: f77ee4bff0a0
Create Date: 2026-10-22 06:10:00

"""
from alembic import op
import sqlalchemy as sa


revision = '3c4a0b3ccc8b'
down_revision = 'f77ee4bff0a0'
branch_labels = None
depends_on = None

FK_NAME = "fk_boxes_plot_id_plots"

plots = sa.table("plots", sa.column("id", sa.Integer), sa.column("name", sa.String),
                 sa.column("active", sa.Boolean))
boxes = sa.table("boxes", sa.column("id", sa.Integer), sa.column("field", sa.String),
                 sa.column("plot_id", sa.Integer))


def upgrade():
    op.create_table('plots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('area_ha', sa.Numeric(precision=7, scale=2), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('boxes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plot_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(FK_NAME, 'plots', ['plot_id'], ['id'])

    # Freitext → Schläge (gleiche Schreibweise ohne Groß/Klein und Leerzeichen = ein Schlag)
    conn = op.get_bind()
    rows = conn.execute(sa.select(boxes.c.id, boxes.c.field).where(boxes.c.field.isnot(None))).fetchall()
    plot_ids = {}
    for box_id, field in rows:
        name = " ".join(field.split())[:100]
        if not name:
            continue
        key = name.lower()
        if key not in plot_ids:
            plot_ids[key] = conn.execute(
                plots.insert().values(name=name, active=True).returning(plots.c.id)).scalar_one()
        conn.execute(boxes.update().where(boxes.c.id == box_id).values(plot_id=plot_ids[key]))

    with op.batch_alter_table('boxes', schema=None) as batch_op:
        batch_op.drop_column('field')


def downgrade():
    with op.batch_alter_table('boxes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('field', sa.String(length=100), nullable=True))
    conn = op.get_bind()
    for plot_id, name in conn.execute(sa.select(plots.c.id, plots.c.name)).fetchall():
        conn.execute(boxes.update().where(boxes.c.plot_id == plot_id).values(field=name))
    with op.batch_alter_table('boxes', schema=None) as batch_op:
        batch_op.drop_constraint(FK_NAME, type_='foreignkey')
        batch_op.drop_column('plot_id')
    op.drop_table('plots')
