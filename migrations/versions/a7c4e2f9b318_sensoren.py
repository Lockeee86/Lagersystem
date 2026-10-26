"""sensoren: Meshtastic-Knoten an einer Stelle, untere Leiste wählbar

Revision ID: a7c4e2f9b318
Revises: fcb2e82411e2
Create Date: 2026-10-26 15:10:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c4e2f9b318'
down_revision = 'fcb2e82411e2'
branch_labels = None
depends_on = None


def upgrade():
    nodes = op.create_table('mesh_nodes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('node', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=True),
    sa.Column('purpose', sa.String(length=20), nullable=True),
    sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True),
    sa.Column('temperature', sa.Float(), nullable=True),
    sa.Column('humidity', sa.Float(), nullable=True),
    sa.Column('co2', sa.Integer(), nullable=True),
    sa.Column('battery', sa.Float(), nullable=True),
    sa.Column('voltage', sa.Float(), nullable=True),
    sa.Column('rssi', sa.Float(), nullable=True),
    sa.Column('snr', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('node', name='uq_mesh_nodes_node')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tabbar', sa.String(length=60), server_default='', nullable=False))

    # Bisherige Zuordnung übernehmen: Stall-Knoten aus den Einstellungen (sonst der zuletzt messende Knoten des
    # Stalls bzw. des oberen Kontakts), Lager-Sensoren aus climate_sensors.
    conn = op.get_bind()
    now = sa.func.now()
    rows = {}
    settings = dict(conn.execute(sa.text(
        "SELECT key, value FROM settings WHERE key IN ('coop_node', 'coop_top_node')")).fetchall())
    coop = settings.get('coop_node') or conn.execute(sa.text(
        "SELECT node FROM coop_readings WHERE node IS NOT NULL AND temperature IS NOT NULL "
        "ORDER BY at DESC LIMIT 1")).scalar()
    top = settings.get('coop_top_node') or conn.execute(sa.text(
        "SELECT node FROM coop_door_events WHERE node IS NOT NULL AND position = 'oben' "
        "ORDER BY at DESC LIMIT 1")).scalar()
    if coop:
        rows[coop] = {'node': coop, 'name': None, 'purpose': 'coop'}
    if top and top != coop:
        rows[top] = {'node': top, 'name': None, 'purpose': 'coop_top'}
    for node, name, warehouse_id, active in conn.execute(sa.text(
            "SELECT node, name, warehouse_id, active FROM climate_sensors")).fetchall():
        if node in rows:
            continue
        purpose = ('climate' if warehouse_id else None) if active else 'ignore'
        rows[node] = {'node': node, 'name': name, 'purpose': purpose}
    for row in rows.values():
        conn.execute(nodes.insert().values(**row, created_at=now))
    # zuletzt gemeldet: aus den bisherigen Messwerten
    conn.execute(sa.text(
        "UPDATE mesh_nodes SET last_seen = (SELECT MAX(at) FROM coop_readings r WHERE r.node = mesh_nodes.node) "
        "WHERE purpose IN ('coop', 'coop_top')"))
    conn.execute(sa.text(
        "UPDATE mesh_nodes SET last_seen = (SELECT MAX(r.at) FROM climate_readings r JOIN climate_sensors s "
        "ON s.id = r.sensor_id WHERE s.node = mesh_nodes.node) WHERE last_seen IS NULL"))
    conn.execute(sa.text("DELETE FROM settings WHERE key IN ('coop_node', 'coop_top_node')"))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('tabbar')
    op.drop_table('mesh_nodes')
