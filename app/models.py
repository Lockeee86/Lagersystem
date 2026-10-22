"""Datenbankmodelle des Lagersystems.

Alle Zeitstempel werden in UTC gespeichert und erst bei der Anzeige
in die lokale Zeitzone umgerechnet.
"""
import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .i18n import DEFAULT_LANGUAGE, N_, gettext as _


def utcnow():
    return datetime.now(timezone.utc)


# --- Rollen -----------------------------------------------------------------

ROLE_ADMIN = "admin"
ROLE_MITARBEITER = "mitarbeiter"
ROLE_BETRACHTER = "betrachter"

ROLES = {
    ROLE_ADMIN: N_("Admin"),
    ROLE_MITARBEITER: N_("Mitarbeiter"),
    ROLE_BETRACHTER: N_("Betrachter"),
}
# Ansichten der Lagerkarte
MAP_VIEWS = {
    "karte": N_("Karte"),
    "3d": N_("3D"),
    "liste": N_("Liste"),
}

# Höherer Wert = mehr Rechte
ROLE_LEVEL = {ROLE_BETRACHTER: 1, ROLE_MITARBEITER: 2, ROLE_ADMIN: 3}


class Setting(db.Model):
    """Einstellungen des Betriebs als Schlüssel/Wert (Hofname, Anschrift …)."""
    __tablename__ = "settings"

    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text)

    # Schlüssel: (Bezeichnung, maximale Länge, mehrzeilig)
    FIELDS = {
        "farm_name": (N_("Hofname"), 100, False),
        "farm_address": (N_("Anschrift"), 500, True),
        "farm_phone": (N_("Telefon"), 50, False),
        "farm_email": (N_("E-Mail"), 120, False),
        "farm_passport_reg": (N_("Registriernummer für den Pflanzenpass (z. B. DE-NI1-12345)"), 40, False),
        "farm_location": (N_("Standort für das Wetter (Breite, Länge)"), 60, False),
        "blight_from_month": (N_("Krautfäule-Ampel auf der Übersicht ab"), 2, False),
        "blight_to_month": (N_("bis einschließlich"), 2, False),
    }
    DEFAULTS = {"blight_from_month": "5", "blight_to_month": "9"}

    @classmethod
    def all(cls):
        values = {key: cls.DEFAULTS.get(key, "") for key in cls.FIELDS}
        values.update({s.key: s.value for s in cls.query.filter(cls.key.in_(cls.FIELDS)) if s.value})
        return values

    @classmethod
    def put(cls, key, value):
        setting = db.session.get(cls, key) or cls(key=key)
        setting.value = value or None
        db.session.add(setting)


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
    language = db.Column(db.String(5), nullable=False, default=DEFAULT_LANGUAGE,
                         server_default=DEFAULT_LANGUAGE)
    # Zuletzt gewählte Ansicht der Lagerkarte (siehe MAP_VIEWS)
    map_view = db.Column(db.String(10), nullable=False, default="karte", server_default="karte")
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
        return _(ROLES.get(self.role, self.role))

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
    # Reihen mit Zahlen (1, 2, 3 …) statt Buchstaben (A, B, C …) beschriften
    numeric_rows = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    # Reihe 1 (A) in der Karte unten anzeigen, wie an der Tafel im Lager
    rows_bottom_up = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    boxes = db.relationship("Box", back_populates="warehouse", lazy="dynamic")

    @property
    def capacity(self):
        return self.rows * self.slots * self.max_levels

    def row_label(self, row):
        """Reihe 1 → "A" (oder "1" bei Zahlen). Bei mehr als 26 Reihen immer numerisch."""
        if self.numeric_rows or self.rows > 26:
            return str(row)
        return chr(ord("A") + row - 1)

    def row_labels(self):
        return [(r, self.row_label(r)) for r in range(1, self.rows + 1)]

    def display_rows(self):
        """Reihen in der Anzeigereihenfolge der Karte (bei `rows_bottom_up` A unten)."""
        labels = self.row_labels()
        return labels[::-1] if self.rows_bottom_up else labels

    def location_label(self, row, slot, level=None):
        label = f"{self.row_label(row)}-{slot:02d}"
        if level is not None:
            label += f"-{level}"
        return label


SORT_USAGES = {
    "speise": N_("Speise"),
    "futter": N_("Futter"),
    "pflanzgut": N_("Saat"),
    "sonstiges": N_("Sonstiges"),
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
        return _(SORT_USAGES.get(self.usage, self.usage))


class Plot(db.Model):
    """Schlag (Ackerfläche), von dem die Kartoffeln stammen."""
    __tablename__ = "plots"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    area_ha = db.Column(db.Numeric(7, 2))
    note = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)


class SeedClass(db.Model):
    """Stufe beim Pflanzgut (z. B. PB, SE, E, BE, Z)."""
    __tablename__ = "seed_classes"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)
    description = db.Column(db.String(200))
    position = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True)


class BoxType(db.Model):
    """Kistentyp mit Standardgewicht bei voller Kiste."""
    __tablename__ = "box_types"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    full_weight_kg = db.Column(db.Integer, nullable=False, default=400)
    note = db.Column(db.Text)


# --- Kisten -----------------------------------------------------------------

FILL_LEVELS = [
    (100, N_("voll")),
    (75, "¾"),
    (50, "½"),
    (25, "¼"),
    (0, N_("leer")),
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
    usage = db.Column(db.String(20))            # Saat / Speise …; leer = wie bei der Sorte
    seed_class_id = db.Column(db.Integer, db.ForeignKey("seed_classes.id"))
    harvest_date = db.Column(db.Date)
    plot_id = db.Column(db.Integer, db.ForeignKey("plots.id"))
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
    plot = db.relationship("Plot")
    seed_class = db.relationship("SeedClass")
    warehouse = db.relationship("Warehouse", back_populates="boxes")
    movements = db.relationship(
        "Movement", back_populates="box", order_by="Movement.created_at.desc()",
        lazy="dynamic", cascade="all, delete-orphan",
    )
    delivery_items = db.relationship("DeliveryItem", back_populates="box", lazy="dynamic",
                                     order_by="DeliveryItem.id.desc()")

    @property
    def effective_usage(self):
        return self.usage or (self.variety.usage if self.variety else None)

    @property
    def usage_label(self):
        usage = self.effective_usage
        return _(SORT_USAGES.get(usage, usage)) if usage else ""

    @property
    def is_stored(self):
        return self.warehouse_id is not None

    @property
    def is_empty(self):
        return self.fill == 0

    @property
    def fill_label(self):
        return _(FILL_LABELS.get(self.fill, f"{self.fill} %"))

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
    "angelegt": (N_("Angelegt"), "bi-plus-circle"),
    "befuellt": (N_("Inhalt geändert"), "bi-basket"),
    "eingelagert": (N_("Eingelagert"), "bi-box-arrow-in-down"),
    "umgelagert": (N_("Umgelagert"), "bi-arrow-left-right"),
    "nachgerueckt": (N_("Nachgerückt"), "bi-arrow-down"),
    "fuellstand": (N_("Füllstand"), "bi-speedometer2"),
    "ausgelagert": (N_("Ausgelagert"), "bi-box-arrow-up"),
    "verladen": (N_("Verladen"), "bi-truck"),
    "zurueckgenommen": (N_("Verladen zurückgenommen"), "bi-arrow-counterclockwise"),
}

# Felder, deren Änderung bei "befuellt" in Movement.detail vermerkt wird
CONTENT_FIELDS = {
    "box_type": N_("Kistentyp"),
    "variety": N_("Sorte"),
    "harvest_date": N_("Erntedatum"),
    "field": N_("Schlag"),
    "grade": N_("Sortierung"),
    "note": N_("Notiz"),
    "usage": N_("Verwendung"),
    "seed_class": N_("Stufe"),
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
    note = db.Column(db.Text)  # Notiz des Benutzers
    # Systemangabe, sprachneutral: bei "nachgerueckt" der Code der entnommenen Kiste,
    # bei "befuellt" die geänderten Felder (Schlüssel aus CONTENT_FIELDS, kommagetrennt)
    detail = db.Column(db.String(200))
    # Momentaufnahme des Kisteninhalts nach der Bewegung (für die Auswertung je Kartoffeljahr)
    variety_id = db.Column(db.Integer, db.ForeignKey("varieties.id"))
    plot_id = db.Column(db.Integer, db.ForeignKey("plots.id"))
    harvest_date = db.Column(db.Date, index=True)
    fill = db.Column(db.Integer)
    usage = db.Column(db.String(20))
    seed_class_id = db.Column(db.Integer, db.ForeignKey("seed_classes.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    box = db.relationship("Box", back_populates="movements")
    user = db.relationship("User")
    variety = db.relationship("Variety")
    plot = db.relationship("Plot")
    seed_class = db.relationship("SeedClass")

    @property
    def action_label(self):
        return _(MOVEMENT_ACTIONS.get(self.action, (self.action, "bi-dot"))[0])

    @property
    def action_icon(self):
        return MOVEMENT_ACTIONS.get(self.action, (self.action, "bi-dot"))[1]

    @property
    def detail_text(self):
        if not self.detail:
            return None
        if self.action == "nachgerueckt":
            return _("{code} wurde darunter entnommen", code=self.detail)
        if self.action in ("verladen", "zurueckgenommen"):
            return _("Lieferschein {number}", number=self.detail)
        if self.action == "befuellt":
            fields = ", ".join(_(CONTENT_FIELDS.get(key, key)) for key in self.detail.split(","))
            return _("Geändert: {fields}", fields=fields)
        return self.detail


# --- Verkauf ----------------------------------------------------------------

class Customer(db.Model):
    """Kunde (Pflanzgut-Abnehmer, Händler …)."""
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    number = db.Column(db.String(30))
    address = db.Column(db.Text)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    note = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    deliveries = db.relationship("Delivery", back_populates="customer", lazy="dynamic")


class Certification(db.Model):
    """Anerkennungsnummer einer Pflanzgut-Partie (Sorte + Stufe + Erntejahr)."""
    __tablename__ = "certifications"
    __table_args__ = (db.UniqueConstraint("variety_id", "seed_class_id", "year", name="uq_certification"),)

    id = db.Column(db.Integer, primary_key=True)
    variety_id = db.Column(db.Integer, db.ForeignKey("varieties.id"), nullable=False)
    seed_class_id = db.Column(db.Integer, db.ForeignKey("seed_classes.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    number = db.Column(db.String(60), nullable=False)
    note = db.Column(db.String(200))

    variety = db.relationship("Variety")
    seed_class = db.relationship("SeedClass")


DELIVERY_OPEN = "offen"
DELIVERY_CLOSED = "abgeschlossen"
DELIVERY_STATUSES = {
    DELIVERY_OPEN: N_("Offen"),
    DELIVERY_CLOSED: N_("Abgeschlossen"),
}


class Delivery(db.Model):
    """Lieferschein. Kisten werden beim Verladen gescannt, abgepackte Ware als Menge erfasst."""
    __tablename__ = "deliveries"

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), unique=True, nullable=False)   # LS-2026-0001
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    vehicle = db.Column(db.String(50))
    # Fuhrwerkswaage: Fahrzeug voll (brutto) und leer (tara)
    gross_kg = db.Column(db.Integer)
    tare_kg = db.Column(db.Integer)
    note = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default=DELIVERY_OPEN)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at = db.Column(db.DateTime(timezone=True))

    customer = db.relationship("Customer", back_populates="deliveries")
    created_by = db.relationship("User")
    items = db.relationship("DeliveryItem", back_populates="delivery", order_by="DeliveryItem.id",
                            cascade="all, delete-orphan")

    @property
    def is_open(self):
        return self.status == DELIVERY_OPEN

    @property
    def status_label(self):
        return _(DELIVERY_STATUSES.get(self.status, self.status))

    @property
    def net_kg(self):
        if self.gross_kg is None or self.tare_kg is None:
            return None
        return self.gross_kg - self.tare_kg

    @property
    def estimated_kg(self):
        return sum(item.weight_kg or 0 for item in self.items)

    @property
    def box_count(self):
        return sum(1 for item in self.items if item.box_id)


class DeliveryItem(db.Model):
    """Position eines Lieferscheins: eine gescannte Kiste oder eine Menge abgepackter Ware.
    Sorte, Stufe usw. werden beim Verladen kopiert, damit der Lieferschein unverändert bleibt."""
    __tablename__ = "delivery_items"

    id = db.Column(db.Integer, primary_key=True)
    delivery_id = db.Column(db.Integer, db.ForeignKey("deliveries.id"), nullable=False, index=True)
    box_id = db.Column(db.Integer, db.ForeignKey("boxes.id"), index=True)
    variety_id = db.Column(db.Integer, db.ForeignKey("varieties.id"))
    usage = db.Column(db.String(20))
    seed_class_id = db.Column(db.Integer, db.ForeignKey("seed_classes.id"))
    grade = db.Column(db.String(50))
    harvest_date = db.Column(db.Date)
    plot_id = db.Column(db.Integer, db.ForeignKey("plots.id"))
    fill = db.Column(db.Integer)            # nur Kisten: Füllstand beim Verladen
    packaging = db.Column(db.String(60))    # nur Menge: z. B. „25-kg-Sack“, „Big Bag“
    quantity = db.Column(db.Integer)        # nur Menge: Anzahl Gebinde
    weight_kg = db.Column(db.Integer)       # Kiste: geschätzt; Menge: Anzahl × Gewicht je Gebinde
    from_location = db.Column(db.String(100))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    delivery = db.relationship("Delivery", back_populates="items")
    box = db.relationship("Box", back_populates="delivery_items")
    variety = db.relationship("Variety")
    seed_class = db.relationship("SeedClass")
    plot = db.relationship("Plot")
    user = db.relationship("User")

    @property
    def usage_label(self):
        return _(SORT_USAGES.get(self.usage, self.usage)) if self.usage else ""

    @property
    def fill_label(self):
        return _(FILL_LABELS.get(self.fill, f"{self.fill} %")) if self.fill is not None else ""


# --- Maschinen --------------------------------------------------------------

MACHINE_CATEGORIES = {
    "schlepper": N_("Schlepper"),
    "fahrzeug": N_("Fahrzeug"),
    "roder": N_("Roder"),
    "legemaschine": N_("Legemaschine"),
    "sortieranlage": N_("Sortieranlage / Band"),
    "anhaenger": N_("Anhänger"),
    "stapler": N_("Stapler / Lader"),
    "lagertechnik": N_("Lagertechnik"),
    "sonstiges": N_("Sonstiges"),
}
COUNTER_UNITS = {"h": N_("Betriebsstunden"), "km": N_("Kilometer")}


class Machine(db.Model):
    """Maschine oder Fahrzeug mit QR-Code (`M-001`), Zählerstand und Wartungsintervallen."""
    __tablename__ = "machines"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(30), nullable=False, default="sonstiges")
    manufacturer = db.Column(db.String(100))
    model = db.Column(db.String(100))
    year = db.Column(db.Integer)
    plate = db.Column(db.String(30))             # Kennzeichen
    serial_no = db.Column(db.String(100))
    counter_unit = db.Column(db.String(5))       # "h", "km" oder leer (ohne Zähler)
    counter = db.Column(db.Integer)              # aktueller Stand
    counter_at = db.Column(db.DateTime(timezone=True))
    note = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    tasks = db.relationship("MaintenanceTask", back_populates="machine", order_by="MaintenanceTask.name",
                            cascade="all, delete-orphan")
    events = db.relationship("MachineEvent", back_populates="machine", lazy="dynamic",
                             order_by=lambda: (MachineEvent.date.desc(), MachineEvent.id.desc()),
                             cascade="all, delete-orphan")

    @property
    def category_label(self):
        return _(MACHINE_CATEGORIES.get(self.category, self.category))

    @property
    def unit_short(self):
        return {"h": "h", "km": "km"}.get(self.counter_unit, "")


class MaintenanceTask(db.Model):
    """Wiederkehrende Wartung: fällig alle `interval_hours` (Zähler) und/oder `interval_months`."""
    __tablename__ = "maintenance_tasks"

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    interval_hours = db.Column(db.Integer)       # in der Einheit des Zählers (h oder km)
    interval_months = db.Column(db.Integer)
    last_done_on = db.Column(db.Date)
    last_done_counter = db.Column(db.Integer)
    note = db.Column(db.String(200))

    machine = db.relationship("Machine", back_populates="tasks")


MACHINE_EVENT_KINDS = {
    "zaehler": (N_("Zählerstand"), "bi-speedometer2"),
    "wartung": (N_("Wartung"), "bi-wrench-adjustable"),
    "reparatur": (N_("Reparatur"), "bi-tools"),
    "stoerung": (N_("Störung"), "bi-exclamation-triangle"),
    "notiz": (N_("Notiz"), "bi-sticky"),
}


class MachineEvent(db.Model):
    """Logbuch-Eintrag einer Maschine."""
    __tablename__ = "machine_events"

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    date = db.Column(db.Date, nullable=False)
    counter = db.Column(db.Integer)
    task_id = db.Column(db.Integer, db.ForeignKey("maintenance_tasks.id", ondelete="SET NULL"))
    task_name = db.Column(db.String(100))        # bleibt lesbar, auch wenn das Intervall gelöscht wird
    text = db.Column(db.Text)
    cost = db.Column(db.Numeric(10, 2))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    # nur Störungen: erledigt?
    resolved_at = db.Column(db.DateTime(timezone=True))
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    machine = db.relationship("Machine", back_populates="events")
    task = db.relationship("MaintenanceTask")
    user = db.relationship("User", foreign_keys=[user_id])
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_id])
    photos = db.relationship("Photo", back_populates="event", order_by="Photo.id", cascade="all, delete-orphan")

    @property
    def kind_label(self):
        return _(MACHINE_EVENT_KINDS.get(self.kind, (self.kind, ""))[0])

    @property
    def kind_icon(self):
        return MACHINE_EVENT_KINDS.get(self.kind, ("", "bi-dot"))[1]

    @property
    def is_open_fault(self):
        return self.kind == "stoerung" and self.resolved_at is None


class Photo(db.Model):
    """Hochgeladenes Foto (verkleinert als JPEG im Upload-Verzeichnis gespeichert)."""
    __tablename__ = "photos"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("machine_events.id"), nullable=False, index=True)
    filename = db.Column(db.String(80), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    event = db.relationship("MachineEvent", back_populates="photos")
