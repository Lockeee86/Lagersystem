"""Datenbankmodelle des Lagersystems.

Alle Zeitstempel werden in UTC gespeichert und erst bei der Anzeige
in die lokale Zeitzone umgerechnet.
"""
import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


# --- Rollen -----------------------------------------------------------------

ROLE_ADMIN = "admin"
ROLE_MITARBEITER = "mitarbeiter"
ROLE_BETRACHTER = "betrachter"

ROLES = {
    ROLE_ADMIN: "Admin",
    ROLE_MITARBEITER: "Mitarbeiter",
    ROLE_BETRACHTER: "Betrachter",
}
# Höherer Wert = mehr Rechte
ROLE_LEVEL = {ROLE_BETRACHTER: 1, ROLE_MITARBEITER: 2, ROLE_ADMIN: 3}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    display_name = db.Column(db.String(100))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_BETRACHTER)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login = db.Column(db.DateTime(timezone=True))
    # Wechselt bei Passwortänderung/Sperre → alle bestehenden Anmeldungen werden ungültig
    session_token = db.Column(db.String(64), unique=True, nullable=False,
                              default=lambda: secrets.token_urlsafe(32))

    def get_id(self):
        return self.session_token

    def rotate_session(self):
        self.session_token = secrets.token_urlsafe(32)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.rotate_session()

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.active

    @property
    def name(self):
        return self.display_name or self.username

    @property
    def role_label(self):
        return ROLES.get(self.role, self.role)

    def has_role(self, role):
        return ROLE_LEVEL.get(self.role, 0) >= ROLE_LEVEL[role]

    @property
    def can_edit(self):
        return self.has_role(ROLE_MITARBEITER)

    @property
    def is_admin(self):
        return self.has_role(ROLE_ADMIN)


# --- Stammdaten -------------------------------------------------------------

class Warehouse(db.Model):
    """Ein Lager(raum) mit Reihen × Stellplätzen × Ebenen."""
    __tablename__ = "warehouses"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    rows = db.Column(db.Integer, nullable=False, default=10)
    slots = db.Column(db.Integer, nullable=False, default=15)
    max_levels = db.Column(db.Integer, nullable=False, default=5)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    boxes = db.relationship("Box", back_populates="warehouse", lazy="dynamic")

    @property
    def capacity(self):
        return self.rows * self.slots * self.max_levels

    def row_label(self, row):
        """Reihe 1 → "A". Bei mehr als 26 Reihen wird numerisch gezählt."""
        if self.rows <= 26:
            return chr(ord("A") + row - 1)
        return str(row)

    def row_labels(self):
        return [(r, self.row_label(r)) for r in range(1, self.rows + 1)]

    def location_label(self, row, slot, level=None):
        label = f"{self.row_label(row)}-{slot:02d}"
        if level is not None:
            label += f"-{level}"
        return label


SORT_USAGES = {
    "speise": "Speise",
    "futter": "Futter",
    "pflanzgut": "Pflanzgut",
    "sonstiges": "Sonstiges",
}


class Variety(db.Model):
    """Kartoffelsorte."""
    __tablename__ = "varieties"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    color = db.Column(db.String(7), nullable=False, default="#c8a24a")
    usage = db.Column(db.String(20), nullable=False, default="speise")
    note = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def usage_label(self):
        return SORT_USAGES.get(self.usage, self.usage)


class BoxType(db.Model):
    """Kistentyp mit Standardgewicht bei voller Kiste."""
    __tablename__ = "box_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    full_weight_kg = db.Column(db.Integer, nullable=False, default=400)
    note = db.Column(db.Text)


# --- Kisten -----------------------------------------------------------------

FILL_LEVELS = [
    (100, "voll"),
    (75, "¾"),
    (50, "½"),
    (25, "¼"),
    (0, "leer"),
]
FILL_LABELS = dict(FILL_LEVELS)


class Box(db.Model):
    __tablename__ = "boxes"
    __table_args__ = (
        db.UniqueConstraint("warehouse_id", "row", "slot", "level", name="uq_box_position"),
        db.Index("ix_box_stack", "warehouse_id", "row", "slot"),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    box_type_id = db.Column(db.Integer, db.ForeignKey("box_types.id"), nullable=False)
    variety_id = db.Column(db.Integer, db.ForeignKey("varieties.id"))
    harvest_date = db.Column(db.Date)
    field = db.Column(db.String(100))
    grade = db.Column(db.String(50))
    fill = db.Column(db.Integer, nullable=False, default=0)
    note = db.Column(db.Text)

    # Aktueller Platz – alle NULL = nicht eingelagert
    warehouse_id = db.Column(db.Integer, db.ForeignKey("warehouses.id"))
    row = db.Column(db.Integer)
    slot = db.Column(db.Integer)
    level = db.Column(db.Integer)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    box_type = db.relationship("BoxType")
    variety = db.relationship("Variety")
    warehouse = db.relationship("Warehouse", back_populates="boxes")
    movements = db.relationship(
        "Movement", back_populates="box", order_by="Movement.created_at.desc()",
        lazy="dynamic", cascade="all, delete-orphan",
    )

    @property
    def is_stored(self):
        return self.warehouse_id is not None

    @property
    def is_empty(self):
        return self.fill == 0

    @property
    def fill_label(self):
        return FILL_LABELS.get(self.fill, f"{self.fill} %")

    @property
    def weight_kg(self):
        if not self.box_type:
            return 0
        return round(self.box_type.full_weight_kg * self.fill / 100)

    @property
    def location(self):
        if not self.is_stored:
            return None
        return self.warehouse.location_label(self.row, self.slot, self.level)

    @property
    def color(self):
        if self.is_empty or not self.variety:
            return None
        return self.variety.color


MOVEMENT_ACTIONS = {
    "angelegt": ("Angelegt", "bi-plus-circle"),
    "befuellt": ("Inhalt geändert", "bi-basket"),
    "eingelagert": ("Eingelagert", "bi-box-arrow-in-down"),
    "umgelagert": ("Umgelagert", "bi-arrow-left-right"),
    "nachgerueckt": ("Nachgerückt", "bi-arrow-down"),
    "fuellstand": ("Füllstand", "bi-speedometer2"),
    "ausgelagert": ("Ausgelagert", "bi-box-arrow-up"),
}


class Movement(db.Model):
    """Protokolleintrag zu einer Kiste."""
    __tablename__ = "movements"

    id = db.Column(db.Integer, primary_key=True)
    box_id = db.Column(db.Integer, db.ForeignKey("boxes.id"), nullable=False, index=True)
    action = db.Column(db.String(20), nullable=False)
    from_location = db.Column(db.String(150))
    to_location = db.Column(db.String(150))
    fill_before = db.Column(db.Integer)
    fill_after = db.Column(db.Integer)
    note = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    box = db.relationship("Box", back_populates="movements")
    user = db.relationship("User")

    @property
    def action_label(self):
        return MOVEMENT_ACTIONS.get(self.action, (self.action, "bi-dot"))[0]

    @property
    def action_icon(self):
        return MOVEMENT_ACTIONS.get(self.action, (self.action, "bi-dot"))[1]
