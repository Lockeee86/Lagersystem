"""Datenbankmodelle des Lagersystems.

Alle Zeitstempel werden in UTC gespeichert und erst bei der Anzeige
in die lokale Zeitzone umgerechnet.
"""
import secrets
from datetime import date, datetime, timedelta, timezone

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
        "holiday_state": (N_("Feiertage im Kalender"), 2, False),
        "aerial_source": (N_("Luftbild unter der Schlagkarte"), 10, False),
        "aerial_wms_url": (N_("WMS-Adresse"), 300, False),
        "aerial_wms_layer": (N_("WMS-Layer"), 100, False),
    }
    DEFAULTS = {"blight_from_month": "5", "blight_to_month": "9", "holiday_state": "NI", "aerial_source": "lgln"}

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
    # Besteller: bearbeitet Bestellwünsche (Admins dürfen das immer)
    can_order = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    can_plan = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    chickens = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())   # Rubrik Hühner
    biogas = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())        # Rubrik Biogas
    # Sieht nur die Hühner (und deren Aufgaben/Stallkontrolle), sonst nichts vom Betrieb – siehe chickens_only.py
    chickens_only = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    # Geheimer Schlüssel für den Kalender-Abo-Link (leer = noch nie abonniert)
    calendar_token = db.Column(db.String(64), unique=True)
    # Kacheln auf der Übersicht: Abweichungen von der Voreinstellung der Rolle (kommagetrennt, siehe tiles.py)
    tiles_add = db.Column(db.String(400), nullable=False, default="", server_default="")
    tiles_hide = db.Column(db.String(400), nullable=False, default="", server_default="")
    # Abgewählte Push-Themen, kommagetrennt (leer = alle passenden an) …
    push_off = db.Column(db.String(300), nullable=False, default="", server_default="")
    # … und eingeschaltete Themen, die für diesen Benutzer standardmäßig aus sind (PUSH_TOPICS "default")
    push_on = db.Column(db.String(300), nullable=False, default="", server_default="")
    # Bereiche der Startseite: Abweichung von der Voreinstellung der Rolle (tiles.SECTIONS)
    home_add = db.Column(db.String(300), nullable=False, default="", server_default="")
    home_hide = db.Column(db.String(300), nullable=False, default="", server_default="")
    home_order = db.Column(db.String(300), nullable=False, default="", server_default="")   # eigene Reihenfolge
    # Untere Leiste am Handy: die zwei freien Plätze („lager,feld“), leer = Voreinstellung der Rolle (tabbar.py)
    tabbar = db.Column(db.String(60), nullable=False, default="", server_default="")
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

    @property
    def is_buyer(self):
        """Darf Bestellwünsche bearbeiten (bestellen, Liefertermin eintragen …)."""
        return self.is_admin or (self.can_order and self.can_edit)

    @property
    def is_planner(self):
        """Darf den Tagesplan pflegen (einteilen, löschen, übernehmen, verschicken)."""
        return self.is_admin or (self.can_plan and self.can_edit)

    @property
    def only_chickens(self):
        """Benutzer „nur Hühner“ (für Admins ohne Wirkung)."""
        return bool(self.chickens_only) and not self.is_admin

    @property
    def sees_biogas(self):
        """Rubrik Biogas (BHKW, Rundgang, Strompreis) – in der Benutzerverwaltung abwählbar, Admins immer."""
        return self.is_admin or (bool(self.biogas) and not self.only_chickens)

    @property
    def sees_chickens(self):
        """Rubrik Hühner in Navigation und Startseite (in der Benutzerverwaltung freigeschaltet)."""
        return bool(self.chickens) or self.only_chickens

    @property
    def keeps_chickens(self):
        """Darf im Hühnerstall eintragen."""
        return self.sees_chickens and self.can_edit

    def push_topics(self):
        """Push-Themen, die für diesen Benutzer in Frage kommen (je nach Rolle)."""
        keys = [key for key, topic in PUSH_TOPICS.items() if topic["who"](self)]
        if not self.sees_biogas:
            keys = [k for k in keys if PUSH_TOPICS[k]["group"] != "Biogas"]
        if self.only_chickens:                         # nur Hühner und eigene Aufgaben
            keys = [k for k in keys if PUSH_TOPICS[k]["group"] == "Hühner" or k in ("tasks_new", "tasks_due")]
        return keys

    def push_default(self, topic):
        default = PUSH_TOPICS[topic].get("default")
        return default is None or bool(default(self))

    def wants_push(self, topic):
        if topic not in self.push_topics():
            return False
        if self.push_default(topic):
            return topic not in (self.push_off or "").split(",")
        return topic in (self.push_on or "").split(",")

    def set_push_topics(self, chosen):
        """Auswahl aus dem Formular speichern: Abweichungen von der Voreinstellung je Thema."""
        topics = self.push_topics()
        self.push_off = ",".join(t for t in topics if self.push_default(t) and t not in chosen)
        self.push_on = ",".join(t for t in topics if not self.push_default(t) and t in chosen)


# Push-Nachrichten: Thema → Bezeichnung, Gruppe, für wen es in Frage kommt, ob es von sich aus an ist
# ("default" fehlt = an)
PUSH_TOPICS = {
    "tasks_new": {"label": N_("Neue Aufgabe für mich"), "group": N_("Aufgaben"), "who": lambda u: u.can_edit},
    "tasks_due": {"label": N_("Morgens: meine Aufgaben für heute"), "group": N_("Aufgaben"), "who": lambda u: u.can_edit},
    "purchase_new": {"label": N_("Neue Bestellwünsche und an mich weitergegebene"), "group": N_("Bestellungen"),
                     "who": lambda u: u.is_buyer},
    "purchase_status": {"label": N_("Neuigkeiten zu meinen Bestellwünschen (bestellt, geliefert …)"),
                        "group": N_("Bestellungen"), "who": lambda u: u.can_edit},
    "fault": {"label": N_("Neue Störung an einer Maschine"), "group": N_("Maschinen"), "who": lambda u: u.can_edit},
    "maintenance": {"label": N_("Wartung wird fällig"), "group": N_("Maschinen"), "who": lambda u: u.can_edit},
    "frost": {"label": N_("Frostwarnung"), "group": N_("Wetter-Warnungen"), "who": lambda u: True,
              "default": lambda u: u.is_admin},
    "blight": {"label": N_("Krautfäule-Ampel springt auf Rot"), "group": N_("Wetter-Warnungen"), "who": lambda u: True,
               "default": lambda u: u.is_admin},
    "tank": {"label": N_("Hoftank wird leer"), "group": N_("Maschinen"), "who": lambda u: u.is_buyer},
    "harvest_report": {"label": N_("Abends: Ernte-Tagesbericht"), "group": N_("Lager"), "who": lambda u: u.can_edit,
                       "default": lambda u: u.is_admin},
    "round_issue": {"label": N_("Rundgang: Mangel an einer Station"), "group": N_("Biogas"),
                    "who": lambda u: u.can_edit, "default": lambda u: u.is_admin},
    "round_due": {"label": N_("Morgens: Stationen, die nicht kontrolliert wurden"), "group": N_("Biogas"),
                  "who": lambda u: u.can_edit, "default": lambda u: u.is_admin},
    "biogas_week": {"label": N_("Montags: Wochenbericht Biogas"), "group": N_("Biogas"), "who": lambda u: u.can_edit,
                    "default": lambda u: u.is_admin},
    "day_plan": {"label": N_("Mein Tagesplan, wenn er verschickt wird"), "group": N_("Team"), "who": lambda u: u.can_edit},
    "handover": {"label": N_("Neue Übergabe-Notiz"), "group": N_("Team"), "who": lambda u: u.can_edit,
                 "default": lambda u: False},
    "eggs_missing": {"label": N_("Abends: noch keine Eier eingetragen"), "group": N_("Hühner"),
                     "who": lambda u: u.keeps_chickens},
    "coop_close": {"label": N_("Zum Sonnenuntergang: Hühnerklappe zu?"), "group": N_("Hühner"),
                   "who": lambda u: u.keeps_chickens, "default": lambda u: False},
    "coop_issue": {"label": N_("Stallkontrolle: Mangel gemeldet"), "group": N_("Hühner"),
                   "who": lambda u: u.keeps_chickens},
    "climate": {"label": N_("Lagerklima außerhalb der Grenzwerte oder Sensor stumm"), "group": N_("Lager"),
                "who": lambda u: u.can_edit, "default": lambda u: u.is_admin},
    "coop_open": {"label": N_("Klappe laut Sensor: abends offen, hängt oder morgens nicht aufgegangen"), "group": N_("Hühner"),
                  "who": lambda u: u.keeps_chickens},
    "coop_sensor": {"label": N_("Stall-Sensor: Frost, Hitze oder keine Verbindung"), "group": N_("Hühner"),
                    "who": lambda u: u.keeps_chickens},
}


class PushSubscription(db.Model):
    """Ein Gerät/Browser, das Push-Nachrichten für einen Benutzer empfängt."""
    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.String(200), nullable=False)
    auth = db.Column(db.String(100), nullable=False)
    device = db.Column(db.String(120))                          # „iPhone · Safari“, nur zur Anzeige
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_sent_at = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User", backref=db.backref("push_subscriptions", cascade="all, delete-orphan",
                                                      passive_deletes=True))


# --- Stammdaten -------------------------------------------------------------

class Site(db.Model):
    """Betriebsstätte (Hofstelle, zweiter Betrieb …) mit eigener Anschrift für den Lieferschein. Schläge, Lager,
    Schüttlager, Maschinen und Lieferscheine können einer Betriebsstätte zugeordnet sein (sonst gelten die
    Angaben des Hofs)."""
    __tablename__ = "sites"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    address = db.Column(db.Text)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    passport_reg = db.Column(db.String(40))       # eigene Pflanzenpass-Registriernummer (sonst die des Hofs)
    note = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())


def site_column():
    return db.Column(db.Integer, db.ForeignKey("sites.id"), index=True)


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
    site_id = site_column()
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    # Lagerklima: Grenzwerte für Warnungen (leer = Voreinstellung aus climate.DEFAULTS)
    climate_temp_min = db.Column(db.Float)
    climate_temp_max = db.Column(db.Float)
    climate_hum_min = db.Column(db.Float)
    climate_hum_max = db.Column(db.Float)
    climate_co2_max = db.Column(db.Integer)

    boxes = db.relationship("Box", back_populates="warehouse", lazy="dynamic")
    site = db.relationship("Site")

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
        """Reihen in der Anzeigereihenfolge von Karte und Liste (bei `rows_bottom_up` A unten; 3D: A vorne)."""
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
    """Schlag (Ackerfläche). Kann aus der Schlagkartei (KML, z. B. Myfarm24) importiert werden."""
    __tablename__ = "plots"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    number = db.Column(db.String(20))           # Schlagnummer aus der Schlagkartei (nicht eindeutig!)
    site_id = site_column()
    area_ha = db.Column(db.Numeric(9, 4))
    soil = db.Column(db.String(60))             # Bodenart
    red_area = db.Column(db.Boolean)            # Rotes Gebiet (Düngeverordnung)
    flik = db.Column(db.String(20))             # Feldblock-Kennung
    myfarm_id = db.Column(db.String(30), index=True)  # „USchlag-ID“ aus der Myfarm24-Schlagdokumentation
    # Bodenuntersuchung (aus der Schlagdokumentation)
    ph = db.Column(db.Numeric(4, 2))
    p_class = db.Column(db.String(5))
    k_class = db.Column(db.String(5))
    mg_value = db.Column(db.Numeric(6, 2))      # mg/100 g Boden
    soil_test_on = db.Column(db.Date)
    # Lage laut Schlagdokumentation (WGS 84) – Punkt auf der Karte, solange kein Umriss da ist
    lat = db.Column(db.Numeric(9, 6))
    lon = db.Column(db.Numeric(9, 6))
    # Umriss als JSON: Liste von Ringen, je Ring [[länge, breite], …] (WGS 84)
    boundary = db.Column(db.Text)
    note = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)

    years = db.relationship("PlotYear", back_populates="plot", order_by="PlotYear.year.desc()",
                            cascade="all, delete-orphan")
    site = db.relationship("Site")

    @property
    def label(self):
        return f"{self.name} ({self.number})" if self.number else self.name

    @property
    def rings(self):
        import json
        try:
            return json.loads(self.boundary) if self.boundary else []
        except ValueError:
            return []

    def year(self, year):
        return next((y for y in self.years if y.year == year), None)


class PlotYear(db.Model):
    """Ein Anbaujahr auf einem Schlag: Kultur (aus der Schlagkartei), Pflanzung und Rodung (in der App)."""
    __tablename__ = "plot_years"
    __table_args__ = (db.UniqueConstraint("plot_id", "year", name="uq_plot_year"),)

    id = db.Column(db.Integer, primary_key=True)
    plot_id = db.Column(db.Integer, db.ForeignKey("plots.id", ondelete="CASCADE"), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False)
    crop = db.Column(db.String(100))
    crop_eppo = db.Column(db.String(10))        # EPPO-Code, z. B. SOLTU = Kartoffel
    variety_id = db.Column(db.Integer, db.ForeignKey("varieties.id"))
    planted_on = db.Column(db.Date)
    seed_note = db.Column(db.String(200))       # Pflanzgut, z. B. Partie/Stufe/Menge
    harvested_on = db.Column(db.Date)
    note = db.Column(db.Text)
    variety_name = db.Column(db.String(100))    # Sorte laut Schlagdokumentation (auch Getreide, Mais …)
    n_demand = db.Column(db.Integer)            # N-Düngebedarf nach DüV in kg/ha
    catch_crop = db.Column(db.String(100))      # Zwischenfrucht
    planned_crop = db.Column(db.String(100))    # laut Fruchtfolgeplan (Myfarm24); "-" = Schlag in dem Jahr nicht im Plan

    plot = db.relationship("Plot", back_populates="years")
    photos = db.relationship("Photo", back_populates="plot_year", order_by="Photo.id", cascade="all, delete-orphan")
    notes = db.relationship("FieldNote", back_populates="plot_year", order_by="FieldNote.created_at.desc()",
                            cascade="all, delete-orphan")
    variety = db.relationship("Variety")
    measures = db.relationship("FieldMeasure", back_populates="plot_year", cascade="all, delete-orphan",
                               order_by=lambda: (FieldMeasure.done_on, FieldMeasure.id))

    @property
    def is_potato(self):
        return self.crop_eppo == "SOLTU" or "kartoffel" in (self.crop or "").lower()

    @property
    def shown_crop(self):
        """Tatsächliche Kultur, sonst die geplante (ohne „-“)."""
        if self.crop:
            return self.crop
        return self.planned_crop if self.planned_crop and self.planned_crop != "-" else None


FIELD_MEASURE_KINDS = {
    "aussaat": N_("Aussaat"),
    "duengung": N_("Düngung"),
    "pflanzenschutz": N_("Pflanzenschutz"),
    "arbeit": N_("Arbeiten / Maschinen"),
    "person": N_("Personen"),
}
NUTRIENTS = ("n_total", "n_avail", "p2o5", "k2o", "mgo", "s", "cao")


class FieldNote(db.Model):
    """Notiz vom Feld (z. B. beim Rundgang: Krautfäule-Befall, Unkraut, Wildschaden) zum Anbaujahr eines Schlags,
    optional mit Standort und Fotos."""
    __tablename__ = "field_notes"

    id = db.Column(db.Integer, primary_key=True)
    plot_year_id = db.Column(db.Integer, db.ForeignKey("plot_years.id", ondelete="CASCADE"), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    lat = db.Column(db.Numeric(9, 6))
    lon = db.Column(db.Numeric(9, 6))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    plot_year = db.relationship("PlotYear", back_populates="notes")
    user = db.relationship("User")
    photos = db.relationship("Photo", back_populates="field_note", order_by="Photo.id", cascade="all, delete-orphan")


class FieldMeasure(db.Model):
    """Eine Maßnahme auf dem Schlag (Aussaat, Düngung, Pflanzenschutz, Arbeit, Person) – aus der
    Myfarm24-Schlagdokumentation (`source` = "myfarm", wird beim nächsten Import ersetzt).
    `name` ist je nach Art Sorte, Dünger, Mittel, Maschine/Arbeit oder Person; bei Personen ist `amount` die Dauer in h.
    Nährstoffe in kg/ha bezogen auf den ganzen Schlag (bei Teilflächen anteilig, wie in Myfarm24)."""
    __tablename__ = "field_measures"

    id = db.Column(db.Integer, primary_key=True)
    plot_year_id = db.Column(db.Integer, db.ForeignKey("plot_years.id", ondelete="CASCADE"), nullable=False,
                             index=True)
    kind = db.Column(db.String(20), nullable=False)
    done_on = db.Column(db.Date)
    bbch = db.Column(db.String(20))
    name = db.Column(db.String(150), nullable=False)
    area_ha = db.Column(db.Numeric(9, 4))
    amount = db.Column(db.Numeric(12, 3))
    unit = db.Column(db.String(20))
    n_total = db.Column(db.Numeric(8, 1))
    n_avail = db.Column(db.Numeric(8, 1))
    p2o5 = db.Column(db.Numeric(8, 1))
    k2o = db.Column(db.Numeric(8, 1))
    mgo = db.Column(db.Numeric(8, 1))
    s = db.Column(db.Numeric(8, 1))
    cao = db.Column(db.Numeric(8, 1))
    note = db.Column(db.String(300))
    source = db.Column(db.String(20), nullable=False, default="myfarm")

    plot_year = db.relationship("PlotYear", back_populates="measures")

    @property
    def kind_label(self):
        return _(FIELD_MEASURE_KINDS.get(self.kind, self.kind))


class PesticideProduct(db.Model):
    """Pflanzenschutzmittel mit Wartezeit (Tage zwischen letzter Anwendung und Ernte) – vom Admin gepflegt.
    Gilt, wenn die Bemerkung der Anwendung keine Wartezeit nennt („Wartezeit 14 Tage“ bzw. „Wartezeit F“)."""
    __tablename__ = "pesticide_products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    wait_days = db.Column(db.Integer)           # None = unbekannt, 0 = keine Wartezeit
    covered = db.Column(db.Boolean, nullable=False, default=False)   # „F“: durch den Anwendungszeitpunkt abgedeckt


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


BOX_DEFECT = "defekt"
BOX_RETIRED = "ausgemustert"
BOX_CONDITIONS = {
    BOX_DEFECT: (N_("Defekt – zur Reparatur"), "bi-wrench-adjustable"),
    BOX_RETIRED: (N_("Ausgemustert"), "bi-x-octagon"),
}


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
    # Zustand der Kiste selbst: leer = in Ordnung, sonst Schlüssel aus BOX_CONDITIONS (Verlauf in Movement)
    condition = db.Column(db.String(20), index=True)
    condition_note = db.Column(db.String(200))      # was kaputt ist
    condition_at = db.Column(db.DateTime(timezone=True))

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
    photos = db.relationship("Photo", back_populates="box", order_by="Photo.id", cascade="all, delete-orphan")

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
    def is_defect(self):
        return self.condition == BOX_DEFECT

    @property
    def is_retired(self):
        return self.condition == BOX_RETIRED

    @property
    def condition_label(self):
        return _(BOX_CONDITIONS[self.condition][0]) if self.condition in BOX_CONDITIONS else ""

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
    "defekt": (N_("Defekt gemeldet"), "bi-wrench-adjustable"),
    "repariert": (N_("Repariert"), "bi-check2-circle"),
    "ausgemustert": (N_("Ausgemustert"), "bi-x-octagon"),
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
    site_id = site_column()                       # Absender (Anschrift der Betriebsstätte auf dem PDF)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at = db.Column(db.DateTime(timezone=True))
    # Unterschriften am Handy (PNG, schwarz auf weiß): Hof = übergeben, Kunde = Ware erhalten
    sign_farm = db.Column(db.LargeBinary)
    sign_farm_name = db.Column(db.String(100))
    sign_farm_at = db.Column(db.DateTime(timezone=True))
    sign_customer = db.Column(db.LargeBinary)
    sign_customer_name = db.Column(db.String(100))
    sign_customer_at = db.Column(db.DateTime(timezone=True))

    customer = db.relationship("Customer", back_populates="deliveries")
    created_by = db.relationship("User")
    items = db.relationship("DeliveryItem", back_populates="delivery", order_by="DeliveryItem.id",
                            cascade="all, delete-orphan")
    photos = db.relationship("Photo", back_populates="delivery", order_by="Photo.id", cascade="all, delete-orphan")
    site = db.relationship("Site")

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

    @property
    def is_signed(self):
        """Mindestens eine Unterschrift – dann sind Positionen, Kunde und Waage gesperrt."""
        return bool(self.sign_farm or self.sign_customer)

    @property
    def fully_signed(self):
        return bool(self.sign_farm and self.sign_customer)


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
    bulk_entry_id = db.Column(db.Integer, db.ForeignKey("bulk_entries.id", ondelete="SET NULL"), index=True)
    crop = db.Column(db.String(100))        # nur Schüttgut: Ware (Kultur, ggf. Sorte) beim Verladen
    fill = db.Column(db.Integer)            # nur Kisten: Füllstand beim Verladen
    packaging = db.Column(db.String(60))    # nur Menge: z. B. „25-kg-Sack“, „Big Bag“
    quantity = db.Column(db.Integer)        # nur Menge: Anzahl Gebinde
    weight_kg = db.Column(db.Integer)       # Kiste: geschätzt; Menge: Anzahl × Gewicht je Gebinde
    from_location = db.Column(db.String(100))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    delivery = db.relationship("Delivery", back_populates="items")
    box = db.relationship("Box", back_populates="delivery_items")
    bulk_entry = db.relationship("BulkEntry")
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


# --- Schüttgut (Getreide u. Ä.) ----------------------------------------------

BULK_KINDS = {
    "box": N_("Flachlager-Box"),
    "haufen": N_("Haufen in der Halle"),
    "bigbag": N_("Big Bags"),
}
BULK_PURPOSES = {
    "verkauf": N_("Verkauf"),
    "futter": N_("Fütterung"),
    "biogas": N_("Biogas"),
    "saatgut": N_("Saatgut (eigene Flächen)"),
    "sonstiges": N_("Sonstiges"),
}
BULK_QUALITY = ("moisture", "protein", "hl_weight", "falling_number", "admixture")


class BulkStore(db.Model):
    """Lagerort für Schüttgut: Flachlager-Box, Haufen in einer Halle oder Big Bags (nur als Anzahl und Menge).
    Enthält immer nur eine Ware (Kultur, optional Sorte); Bestand und Qualität ergeben sich aus den Buchungen."""
    __tablename__ = "bulk_stores"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    code = db.Column(db.String(10), unique=True)  # „S-001“ für das QR-Etikett (Scannen öffnet das Einlagern)
    site_id = site_column()
    kind = db.Column(db.String(20), nullable=False, default="box")
    location = db.Column(db.String(100))          # z. B. „Halle 2“
    capacity_t = db.Column(db.Numeric(10, 2))
    bag_kg = db.Column(db.Integer)                # nur Big Bags: übliches Gewicht je Bag
    crop = db.Column(db.String(100))              # aktuelle Ware; wird beim ersten Einlagern in die leere Zelle gesetzt
    variety_name = db.Column(db.String(100))
    note = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)

    entries = db.relationship("BulkEntry", back_populates="store", cascade="all, delete-orphan",
                              order_by=lambda: (BulkEntry.date.desc(), BulkEntry.id.desc()))
    photos = db.relationship("Photo", back_populates="bulk_store", order_by="Photo.id", cascade="all, delete-orphan")
    site = db.relationship("Site")

    @property
    def kind_label(self):
        return _(BULK_KINDS.get(self.kind, self.kind))

    @property
    def ware(self):
        return " · ".join(v for v in (self.crop, self.variety_name) if v)


class BulkEntry(db.Model):
    """Buchung am Schüttgut-Lager: „ein“ (Fuhre mit Schlag, Gewicht und Qualität) oder „aus“ (Verkauf über
    Lieferschein oder Eigenverbrauch). Gewichte in kg; netto = brutto − tara, wenn beide angegeben sind."""
    __tablename__ = "bulk_entries"

    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey("bulk_stores.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)                  # ein | aus
    date = db.Column(db.Date, nullable=False)
    crop = db.Column(db.String(100))
    variety_name = db.Column(db.String(100))
    plot_id = db.Column(db.Integer, db.ForeignKey("plots.id", ondelete="SET NULL"), index=True)
    gross_kg = db.Column(db.Integer)
    tare_kg = db.Column(db.Integer)
    net_kg = db.Column(db.Integer, nullable=False)
    bags = db.Column(db.Integer)
    moisture = db.Column(db.Numeric(4, 1))         # Feuchte %
    protein = db.Column(db.Numeric(4, 1))          # Protein %
    hl_weight = db.Column(db.Numeric(4, 1))        # Hektolitergewicht kg/hl
    falling_number = db.Column(db.Integer)         # Fallzahl s
    admixture = db.Column(db.Numeric(4, 1))        # Besatz %
    purpose = db.Column(db.String(20))             # nur „aus“: BULK_PURPOSES
    delivery_id = db.Column(db.Integer, db.ForeignKey("deliveries.id", ondelete="SET NULL"), index=True)
    vehicle = db.Column(db.String(50))
    note = db.Column(db.String(300))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    store = db.relationship("BulkStore", back_populates="entries")
    plot = db.relationship("Plot")
    delivery = db.relationship("Delivery")
    user = db.relationship("User")

    @property
    def purpose_label(self):
        return _(BULK_PURPOSES.get(self.purpose, self.purpose)) if self.purpose else ""


# --- Maschinen --------------------------------------------------------------

MACHINE_CATEGORIES = {
    "schlepper": N_("Schlepper"),
    "fahrzeug": N_("Fahrzeug"),
    "maehdrescher": N_("Mähdrescher / Häcksler"),
    "roder": N_("Roder"),
    "legemaschine": N_("Legemaschine"),
    "saetechnik": N_("Sä- und Drilltechnik"),
    "bodenbearbeitung": N_("Bodenbearbeitung (Pflug, Grubber …)"),
    "pflanzenschutz": N_("Pflanzenschutzspritze"),
    "duengung": N_("Düngerstreuer / Güllefass"),
    "sortieranlage": N_("Sortieranlage / Band"),
    "anhaenger": N_("Anhänger"),
    "stapler": N_("Stapler / Lader"),
    "lagertechnik": N_("Lagertechnik"),
    "bhkw": N_("BHKW / Gasmotor"),
    "biogas": N_("Biogasanlage (Rührwerk, Pumpe, Fütterung …)"),
    "sonstiges": N_("Sonstiges"),
}
COUNTER_UNITS = {"h": N_("Betriebsstunden"), "km": N_("Kilometer")}
# Arten mit eigenem Motor und Tank (lässt sich je Maschine umstellen: Machine.refuels)
FUEL_CATEGORIES = {"schlepper", "fahrzeug", "maehdrescher", "roder", "stapler"}
# Symbol je Art (Liste ohne Bild)
MACHINE_ICONS = {"schlepper": "bi-truck-front", "fahrzeug": "bi-car-front", "roder": "bi-basket",
                 "maehdrescher": "bi-wind", "saetechnik": "bi-flower1", "bodenbearbeitung": "bi-tools",
                 "pflanzenschutz": "bi-droplet", "duengung": "bi-moisture",
                 "legemaschine": "bi-grid-3x3", "sortieranlage": "bi-funnel", "anhaenger": "bi-truck-flatbed",
                 "stapler": "bi-box-seam", "lagertechnik": "bi-building-gear", "bhkw": "bi-lightning-charge",
                 "biogas": "bi-recycle", "sonstiges": "bi-gear-wide-connected"}


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
    site_id = site_column()
    image = db.Column(db.String(80))             # Titelbild (Datei in UPLOAD_DIR, Vorschau `uploads.thumb_name`)
    # Wird getankt? leer = nach Art (FUEL_CATEGORIES), sonst ausdrücklich ja/nein
    refuels = db.Column(db.Boolean)
    counter_unit = db.Column(db.String(5))       # "h", "km" oder leer (ohne Zähler)
    counter = db.Column(db.Integer)              # aktueller Stand
    counter_at = db.Column(db.DateTime(timezone=True))
    note = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    # BHKW: Motor in der Speicher-Kraftwerk-API (SKVE) und Nennleistung. Die Betriebsstunden rechnet biogas.sync
    # aus dem Fahrplan: Zählerstand = letzter abgelesener Stand (skve_base_*) + Stunden der Tage danach.
    skve_chp_id = db.Column(db.Integer, unique=True)
    power_kw = db.Column(db.Integer)
    skve_base_counter = db.Column(db.Numeric(10, 1))
    skve_base_day = db.Column(db.Date)

    site = db.relationship("Site")
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

    @property
    def icon(self):
        return MACHINE_ICONS.get(self.category, "bi-gear-wide-connected")

    @property
    def uses_fuel(self):
        """Hat einen eigenen Tank (Schlepper, Fahrzeug …) – Anhänger, Anbaugeräte und Anlagen nicht."""
        return self.refuels if self.refuels is not None else self.category in FUEL_CATEGORIES


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
    downtime_hours = db.Column(db.Integer)       # BHKW: wie lange der Motor dafür steht (fürs Wartungsfenster)

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
    """Hochgeladenes Foto (verkleinert als JPEG im Upload-Verzeichnis gespeichert), gehört zu einem
    Maschinen-Logbucheintrag, einer Aufgabe, einem Bestellwunsch, einer Kiste, einem Anbaujahr, einem
    Schüttlager oder einem Lieferschein. Wird es gelöscht, verschwindet die Datei nach dem Commit (uploads.py)."""
    __tablename__ = "photos"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("machine_events.id"), index=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), index=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchase_requests.id"), index=True)
    box_id = db.Column(db.Integer, db.ForeignKey("boxes.id"), index=True)
    plot_year_id = db.Column(db.Integer, db.ForeignKey("plot_years.id"), index=True)
    bulk_store_id = db.Column(db.Integer, db.ForeignKey("bulk_stores.id"), index=True)
    delivery_id = db.Column(db.Integer, db.ForeignKey("deliveries.id"), index=True)
    field_note_id = db.Column(db.Integer, db.ForeignKey("field_notes.id"), index=True)
    check_id = db.Column(db.Integer, db.ForeignKey("station_checks.id", ondelete="CASCADE"), index=True)
    filename = db.Column(db.String(80), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    event = db.relationship("MachineEvent", back_populates="photos")
    task = db.relationship("Task", back_populates="photos")
    purchase = db.relationship("PurchaseRequest", back_populates="photos")
    box = db.relationship("Box", back_populates="photos")
    plot_year = db.relationship("PlotYear", back_populates="photos")
    bulk_store = db.relationship("BulkStore", back_populates="photos")
    delivery = db.relationship("Delivery", back_populates="photos")
    field_note = db.relationship("FieldNote", back_populates="photos")
    user = db.relationship("User")


# --- Aufgaben ---------------------------------------------------------------

RECURRENCES = {
    "taeglich": N_("täglich"),
    "werktags": N_("werktags (Mo–Fr)"),
    "woechentlich": N_("wöchentlich"),
    "zweiwoechentlich": N_("alle 2 Wochen"),
    "monatlich": N_("monatlich"),
    "vierteljaehrlich": N_("alle 3 Monate"),
    "halbjaehrlich": N_("alle 6 Monate"),
}

task_assignees = db.Table(
    "task_assignees",
    db.Column("task_id", db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)

task_plots = db.Table(
    "task_plots",
    db.Column("task_id", db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    db.Column("plot_id", db.Integer, db.ForeignKey("plots.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class Task(db.Model):
    """Aufgabe für eine oder mehrere Personen (keine Zuteilung = für alle). Einer hakt für alle ab.
    Wiederkehrende Aufgaben erzeugen beim Abhaken die nächste (`previous_id` verkettet die Serie)."""
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    due_on = db.Column(db.Date, index=True)
    important = db.Column(db.Boolean, nullable=False, default=False)
    recurrence = db.Column(db.String(20))
    # Bezug (alles optional)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id", ondelete="SET NULL"))
    delivery_id = db.Column(db.Integer, db.ForeignKey("deliveries.id", ondelete="SET NULL"))
    warehouse_id = db.Column(db.Integer, db.ForeignKey("warehouses.id", ondelete="SET NULL"))
    row = db.Column(db.Integer)
    area = db.Column(db.String(20), index=True)      # Rubrik, aus der die Aufgabe stammt (huehner)
    # Aus einer Störung bzw. fälligen Wartung entstanden: Abhaken erledigt beides
    machine_event_id = db.Column(db.Integer, db.ForeignKey("machine_events.id", ondelete="SET NULL"), index=True)
    maintenance_task_id = db.Column(db.Integer, db.ForeignKey("maintenance_tasks.id", ondelete="SET NULL"), index=True)
    # Erledigt
    done_at = db.Column(db.DateTime(timezone=True), index=True)
    done_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    done_note = db.Column(db.Text)
    previous_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="SET NULL"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    assignees = db.relationship("User", secondary=task_assignees, order_by="User.username")
    machine = db.relationship("Machine")
    delivery = db.relationship("Delivery")
    warehouse = db.relationship("Warehouse")
    plots = db.relationship("Plot", secondary=task_plots, order_by="Plot.name")
    machine_event = db.relationship("MachineEvent")
    maintenance_task = db.relationship("MaintenanceTask")
    done_by = db.relationship("User", foreign_keys=[done_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    photos = db.relationship("Photo", back_populates="task", order_by="Photo.id", cascade="all, delete-orphan")

    @property
    def is_done(self):
        return self.done_at is not None

    @property
    def recurrence_label(self):
        return _(RECURRENCES.get(self.recurrence, self.recurrence)) if self.recurrence else ""

    @property
    def location_label(self):
        if not self.warehouse:
            return None
        if self.row:
            return f"{self.warehouse.name} · {_('Reihe {row}', row=self.warehouse.row_label(self.row))}"
        return self.warehouse.name

    def is_for(self, user):
        return not self.assignees or user in self.assignees


# --- Bestellungen -----------------------------------------------------------

PURCHASE_STATUSES = {
    "neu": (N_("Neu"), "warning"),                       # gemeldet, noch nichts unternommen
    "angefragt": (N_("Angefragt"), "info"),              # beim Lieferanten angefragt (Preis/Verfügbarkeit)
    "bestellt": (N_("Bestellt"), "primary"),
    "geliefert": (N_("Geliefert"), "success"),
    "erledigt": (N_("Erledigt"), "dark"),
    "abgelehnt": (N_("Abgelehnt"), "danger"),
}
PURCHASE_OPEN = ("neu", "angefragt", "bestellt", "geliefert")


class PurchaseRequest(db.Model):
    """Bestellwunsch (Ersatzteil, Verbrauchsmaterial …): Mitarbeiter fragen an, der Besteller bestellt."""
    __tablename__ = "purchase_requests"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)            # Was wird gebraucht?
    details = db.Column(db.Text)                                 # Teilenummer, Hersteller, Maße …
    quantity = db.Column(db.String(50))                          # „2 Stück“, „20 l“
    urgent = db.Column(db.Boolean, nullable=False, default=False)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id", ondelete="SET NULL"), index=True)
    machine_event_id = db.Column(db.Integer, db.ForeignKey("machine_events.id", ondelete="SET NULL"))
    status = db.Column(db.String(20), nullable=False, default="neu", index=True)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    # vom Besteller
    supplier = db.Column(db.String(150))
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id", ondelete="SET NULL"))   # wenn aus dem Verzeichnis
    part_id = db.Column(db.Integer, db.ForeignKey("machine_parts.id", ondelete="SET NULL"), index=True)
    order_number = db.Column(db.String(80))
    price = db.Column(db.Numeric(10, 2))
    inquired_on = db.Column(db.Date)                              # beim Lieferanten angefragt
    ordered_on = db.Column(db.Date)
    expected_on = db.Column(db.Date)
    delivered_on = db.Column(db.Date)
    buyer_note = db.Column(db.Text)                               # auch Begründung bei Ablehnung
    handled_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)   # welcher Besteller kümmert sich
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    machine = db.relationship("Machine")
    machine_event = db.relationship("MachineEvent")
    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    handled_by = db.relationship("User", foreign_keys=[handled_by_id])
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    supplier_ref = db.relationship("Supplier")
    part = db.relationship("MachinePart", back_populates="purchases")
    photos = db.relationship("Photo", back_populates="purchase", order_by="Photo.id", cascade="all, delete-orphan")

    @property
    def status_label(self):
        return _(PURCHASE_STATUSES.get(self.status, (self.status, ""))[0])

    @property
    def status_color(self):
        return PURCHASE_STATUSES.get(self.status, ("", "secondary"))[1]

    @property
    def is_open(self):
        return self.status in PURCHASE_OPEN

    def can_handle(self, user):
        """Bearbeiten darf, wer übernommen hat, der Admin – oder jeder Besteller, solange niemand übernommen hat."""
        return user.is_buyer and (user.is_admin or self.assignee_id in (None, user.id))


# --- Lieferanten, Ersatzteile, Dokumente, Tankbuch -------------------------------

class Supplier(db.Model):
    """Lieferant (Landhandel, Werkstatt, Teilehändler …) mit unserer Kundennummer."""
    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    contact = db.Column(db.String(100))                 # Ansprechpartner
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    website = db.Column(db.String(200))
    customer_no = db.Column(db.String(50))              # unsere Kundennummer dort
    address = db.Column(db.Text)
    note = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class MachinePart(db.Model):
    """Ersatzteil einer Maschine (Filter, Riemen, Messer …) – zum schnellen Nachbestellen."""
    __tablename__ = "machine_parts"

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    part_number = db.Column(db.String(80))
    manufacturer = db.Column(db.String(100))
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id", ondelete="SET NULL"))
    quantity = db.Column(db.String(50))                 # übliche Bestellmenge, z. B. „2 Stück“
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    machine = db.relationship("Machine", backref=db.backref("parts", order_by="MachinePart.name",
                                                            cascade="all, delete-orphan", passive_deletes=True))
    supplier = db.relationship("Supplier")
    purchases = db.relationship("PurchaseRequest", back_populates="part",
                                order_by="PurchaseRequest.created_at.desc()")

    @property
    def last_purchase(self):
        return self.purchases[0] if self.purchases else None


DOCUMENT_KINDS = {
    "anleitung": N_("Bedienungsanleitung"),
    "pruefung": N_("Prüfbericht / TÜV / UVV"),
    "rechnung": N_("Rechnung"),
    "teile": N_("Ersatzteilliste"),
    "sonstiges": N_("Sonstiges"),
}


class MachineDocument(db.Model):
    """PDF oder Bild an einer Maschine (unverändert im Upload-Verzeichnis unter `dokumente/`)."""
    __tablename__ = "machine_documents"

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(150), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="sonstiges")
    filename = db.Column(db.String(80), nullable=False, unique=True)
    original_name = db.Column(db.String(200))
    mimetype = db.Column(db.String(50), nullable=False)
    size = db.Column(db.Integer, nullable=False, default=0)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    machine = db.relationship("Machine", backref=db.backref("documents", order_by="MachineDocument.created_at.desc()",
                                                            cascade="all, delete-orphan", passive_deletes=True))
    user = db.relationship("User")

    @property
    def kind_label(self):
        return _(DOCUMENT_KINDS.get(self.kind, self.kind))

    @property
    def is_pdf(self):
        return self.mimetype == "application/pdf"


FUEL_KINDS = {
    "tanken": N_("Getankt"),            # aus dem Hoftank in eine Maschine
    "lieferung": N_("Lieferung"),       # Tankwagen füllt den Hoftank
    "peilung": N_("Peilung"),           # gemessener Füllstand (setzt den Stand neu)
}


class FuelEntry(db.Model):
    """Tankbuch des Hoftanks (Diesel)."""
    __tablename__ = "fuel_entries"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    liters = db.Column(db.Numeric(10, 1), nullable=False)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    counter = db.Column(db.Integer)                      # Zählerstand der Maschine beim Tanken
    price_per_liter = db.Column(db.Numeric(6, 3))        # bei Lieferungen (netto oder brutto – wie auf der Rechnung)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id", ondelete="SET NULL"))
    note = db.Column(db.String(200))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    machine = db.relationship("Machine", backref=db.backref("fuel_entries", lazy="dynamic", passive_deletes=True))
    supplier = db.relationship("Supplier")
    user = db.relationship("User")

    @property
    def kind_label(self):
        return _(FUEL_KINDS.get(self.kind, self.kind))


# --- Kalender: eigene Termine ---------------------------------------------------

CALENDAR_KINDS = {                        # Art → (Bezeichnung, Bootstrap-Farbe, Symbol)
    "termin": (N_("Termin"), "info", "bi-calendar-event"),
    "frist": (N_("Frist"), "danger", "bi-hourglass-split"),
    "abwesenheit": (N_("Abwesenheit / Urlaub"), "dark", "bi-person-dash"),
    "pruefung": (N_("Prüfung / Kontrolle"), "warning", "bi-clipboard-check"),
    "sonstiges": (N_("Sonstiges"), "secondary", "bi-bookmark"),
}
CALENDAR_REPEATS = {"woechentlich": N_("wöchentlich"), "monatlich": N_("monatlich"), "jaehrlich": N_("jährlich")}

calendar_participants = db.Table(
    "calendar_participants",
    db.Column("entry_id", db.Integer, db.ForeignKey("calendar_entries.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class CalendarEntry(db.Model):
    """Eigener Kalendereintrag (für alle sichtbar). Ohne Uhrzeit = ganztägig; `end_date` für mehrtägige Termine."""
    __tablename__ = "calendar_entries"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="termin")
    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date)                          # leer = eintägig
    start_time = db.Column(db.Time)                        # leer = ganztägig
    end_time = db.Column(db.Time)
    location = db.Column(db.String(200))
    note = db.Column(db.Text)
    repeat = db.Column(db.String(20))                      # siehe CALENDAR_REPEATS
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    participants = db.relationship("User", secondary=calendar_participants, order_by="User.username")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def kind_label(self):
        return _(CALENDAR_KINDS.get(self.kind, CALENDAR_KINDS["sonstiges"])[0])

    @property
    def color(self):
        return CALENDAR_KINDS.get(self.kind, CALENDAR_KINDS["sonstiges"])[1]

    @property
    def icon(self):
        return CALENDAR_KINDS.get(self.kind, CALENDAR_KINDS["sonstiges"])[2]

    @property
    def all_day(self):
        return self.start_time is None

    @property
    def days(self):
        """Dauer in Tagen (1 = eintägig)."""
        return ((self.end_date or self.start_date) - self.start_date).days + 1

    def is_for(self, user):
        return not self.participants or user in self.participants

    def can_edit(self, user):
        return user.is_admin or (user.can_edit and self.created_by_id == user.id)


# --- Änderungsprotokoll -----------------------------------------------------------

class AuditEntry(db.Model):
    """Wer hat wann was geändert oder gelöscht (vorher → nachher), automatisch beim Speichern erfasst (audit.py).
    Gespeichert werden Tabellen- und Feldnamen und die Werte, keine fertigen Sätze – die Anzeige übersetzt."""
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action = db.Column(db.String(20), nullable=False)       # geaendert, geloescht, import
    table = db.Column(db.String(50), nullable=False, index=True)
    object_id = db.Column(db.String(50))
    label = db.Column(db.String(200))                        # Name/Code des Datensatzes zum Zeitpunkt der Änderung
    changes = db.Column(db.JSON)                              # {feld: [vorher, nachher]} bzw. {feld: wert} beim Löschen

    user = db.relationship("User")


# --- Team: Übergabe, Tagesplan, Unterweisungen ----------------------------------------

class Handover(db.Model):
    """Übergabe-Notiz zum Feierabend („Was der Nächste wissen muss“). Steht oben auf der Startseite, bis sie jemand
    als erledigt abhakt oder sie älter als team.HANDOVER_DAYS ist."""
    __tablename__ = "handovers"

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    done_at = db.Column(db.DateTime(timezone=True))
    done_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    user = db.relationship("User", foreign_keys=[user_id])
    done_by = db.relationship("User", foreign_keys=[done_by_id])

plan_entry_plots = db.Table(
    "plan_entry_plots",
    db.Column("entry_id", db.Integer, db.ForeignKey("plan_entries.id", ondelete="CASCADE"), primary_key=True),
    db.Column("plot_id", db.Integer, db.ForeignKey("plots.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class PlanEntry(db.Model):
    """Eintrag im Tagesplan: wer macht an einem Tag was (optional mit Maschine und Schlägen)."""
    __tablename__ = "plan_entries"

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    text = db.Column(db.String(200), nullable=False)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id", ondelete="SET NULL"))
    position = db.Column(db.Integer, nullable=False, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    done_at = db.Column(db.DateTime(timezone=True))
    done_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    user = db.relationship("User", foreign_keys=[user_id])
    done_by = db.relationship("User", foreign_keys=[done_by_id])
    machine = db.relationship("Machine")
    plots = db.relationship("Plot", secondary=plan_entry_plots, order_by="Plot.name")

    def can_check(self, user):
        """Abhaken darf die eingeteilte Person und wer den Tagesplan pflegt."""
        return bool(user and user.is_authenticated and (user.id == self.user_id or user.is_planner))

    @property
    def label(self):                             # fürs Änderungsprotokoll
        return f"{self.day.strftime('%d.%m.%Y')}: {self.text}"


instruction_topic_users = db.Table(
    "instruction_topic_users",
    db.Column("topic_id", db.Integer, db.ForeignKey("instruction_topics.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class InstructionTopic(db.Model):
    """Unterweisung (Stapler, Schlepper, Arbeitsschutz …) mit Wiederholung in Monaten. Gilt für alle Mitarbeiter
    oder nur für ausgewählte."""
    __tablename__ = "instruction_topics"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    content = db.Column(db.Text)                 # Inhalt / Stichpunkte, steht auch auf dem Nachweis
    interval_months = db.Column(db.Integer, nullable=False, default=12)
    for_all = db.Column(db.Boolean, nullable=False, default=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    users = db.relationship("User", secondary=instruction_topic_users, order_by="User.username")
    records = db.relationship("InstructionRecord", back_populates="topic", cascade="all, delete-orphan",
                              order_by="InstructionRecord.day.desc(), InstructionRecord.id.desc()")
    documents = db.relationship("InstructionDocument", back_populates="topic", cascade="all, delete-orphan",
                                order_by="InstructionDocument.created_at")


class InstructionDocument(db.Model):
    """Unterlage zu einer Unterweisung (PDF, Präsentation der Berufsgenossenschaft …), Datei unter `dokumente/`."""
    __tablename__ = "instruction_documents"

    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("instruction_topics.id", ondelete="CASCADE"), nullable=False,
                         index=True)
    title = db.Column(db.String(150), nullable=False)
    filename = db.Column(db.String(80), nullable=False, unique=True)
    original_name = db.Column(db.String(200))
    mimetype = db.Column(db.String(100), nullable=False)
    size = db.Column(db.Integer, nullable=False, default=0)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    topic = db.relationship("InstructionTopic", back_populates="documents")

    @property
    def extension(self):
        return self.filename.rsplit(".", 1)[-1]

    @property
    def viewable(self):
        """Im Browser anzeigbar (PDF, Bild) – Präsentationen werden heruntergeladen."""
        return self.mimetype in ("application/pdf", "image/jpeg", "image/png")

    @property
    def icon(self):
        ext = self.extension
        return ("bi-file-earmark-pdf" if ext == "pdf" else "bi-file-earmark-image" if ext in ("jpg", "png")
                else "bi-file-earmark-slides" if ext in ("pptx", "ppsx", "ppt", "pps", "odp")
                else "bi-file-earmark-spreadsheet" if ext in ("xlsx", "xls", "ods") else "bi-file-earmark-text")


class InstructionRecord(db.Model):
    """Durchgeführte Unterweisung eines Mitarbeiters mit seiner Unterschrift (PNG, siehe signatures.py)."""
    __tablename__ = "instruction_records"

    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("instruction_topics.id", ondelete="CASCADE"), nullable=False,
                         index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    day = db.Column(db.Date, nullable=False)
    signature = db.Column(db.LargeBinary, nullable=False)
    signed_name = db.Column(db.String(100), nullable=False)       # Name zum Zeitpunkt der Unterschrift
    instructor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    instructor_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    topic = db.relationship("InstructionTopic", back_populates="records")
    user = db.relationship("User", foreign_keys=[user_id])
    instructor = db.relationship("User", foreign_keys=[instructor_id])

    @property
    def label(self):
        return f"{self.topic.name}: {self.signed_name} {self.day.strftime('%d.%m.%Y')}"


# --- Beregnung (Import aus RainDancer) ---------------------------------------------------

class Well(db.Model):
    """Brunnen bzw. Entnahmestelle mit genehmigter Jahresmenge (wasserrechtliche Erlaubnis, Kalenderjahr)."""
    __tablename__ = "wells"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    number = db.Column(db.String(30))
    permit_m3 = db.Column(db.Integer)
    note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    @property
    def label(self):
        return f"{self.name} ({self.number})" if self.number else self.name


class Irrigation(db.Model):
    """Ein Beregnungseinsatz (aus dem RainDancer-Wasserbuch). Ein neuer Import ersetzt den Zeitraum der Datei."""
    __tablename__ = "irrigations"

    id = db.Column(db.Integer, primary_key=True)
    well_id = db.Column(db.Integer, db.ForeignKey("wells.id", ondelete="SET NULL"), index=True)
    plot_id = db.Column(db.Integer, db.ForeignKey("plots.id", ondelete="SET NULL"), index=True)
    plot_name = db.Column(db.String(100), nullable=False)       # Schlag laut Datei
    sprinkler = db.Column(db.String(100))
    start_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    end_at = db.Column(db.DateTime(timezone=True))
    hours = db.Column(db.Numeric(7, 1))
    m3 = db.Column(db.Numeric(10, 2), nullable=False)
    source = db.Column(db.String(20), nullable=False, default="raindancer")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    well = db.relationship("Well")
    plot = db.relationship("Plot")

    @property
    def mm(self):
        """Niederschlagshöhe auf dem Schlag: m³ ÷ (ha × 10)."""
        area = self.plot.area_ha if self.plot else None
        return float(self.m3) / (float(area) * 10) if area else None


class ChpDay(db.Model):
    """Ein Tag eines BHKW-Motors aus dem SKVE-Fahrplan: Laufstunden, erzeugte kWh und Erlös zum Day-Ahead-Preis."""
    __tablename__ = "chp_days"
    __table_args__ = (db.UniqueConstraint("machine_id", "day", name="uq_chp_day"),)

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    day = db.Column(db.Date, nullable=False, index=True)
    hours = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    kwh = db.Column(db.Numeric(10, 1), nullable=False, default=0)
    revenue_eur = db.Column(db.Numeric(10, 2))
    starts = db.Column(db.Integer, nullable=False, default=0)       # wie oft der Motor angefahren ist

    machine = db.relationship("Machine", backref=db.backref("chp_days", lazy="dynamic", cascade="all, delete-orphan",
                                                            passive_deletes=True))


# --- Rundgang (Eigenkontrolle mit QR-Stationen) ----------------------------------------

class CheckStation(db.Model):
    """Station für den Rundgang (Fermenter, Pumpenhaus, BHKW-Raum …) mit QR-Code `R-01` und einer Checkliste."""
    __tablename__ = "check_stations"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    items = db.Column(db.Text)                     # Prüfpunkte, einer je Zeile
    interval_days = db.Column(db.Integer, nullable=False, default=1)   # spätestens alle n Tage kontrollieren
    position = db.Column(db.Integer, nullable=False, default=0)
    area = db.Column(db.String(20), nullable=False, default="biogas", server_default="biogas")   # biogas | huehner
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    checks = db.relationship("StationCheck", back_populates="station", lazy="dynamic", cascade="all, delete-orphan",
                             order_by="StationCheck.created_at.desc()")

    @property
    def item_list(self):
        return [line.strip() for line in (self.items or "").splitlines() if line.strip()]


class StationCheck(db.Model):
    """Eine Kontrolle an einer Station: je Prüfpunkt in Ordnung oder nicht, Notiz, Fotos."""
    __tablename__ = "station_checks"

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("check_stations.id", ondelete="CASCADE"), nullable=False,
                           index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    results = db.Column(db.JSON)                   # [[Prüfpunkt, true/false], …] – Text so, wie er damals hieß
    ok = db.Column(db.Boolean, nullable=False, default=True)
    note = db.Column(db.Text)
    resolved_at = db.Column(db.DateTime(timezone=True))      # Mangel behoben
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    station = db.relationship("CheckStation", back_populates="checks")
    user = db.relationship("User", foreign_keys=[user_id])
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_id])
    photos = db.relationship("Photo", order_by="Photo.id", cascade="all, delete-orphan")

    @property
    def failed(self):
        return [item for item, ok in (self.results or []) if not ok]


# --- Hühner ---------------------------------------------------------------------------

FLOCK_REASONS = {                                   # Zugang (+) bzw. Abgang (−) in der Herde
    "gekauft": (1, N_("gekauft")),
    "geschluepft": (1, N_("geschlüpft")),
    "zugang": (1, N_("sonstiger Zugang")),
    "verendet": (-1, N_("verendet")),
    "geschlachtet": (-1, N_("geschlachtet")),
    "raubtier": (-1, N_("Raubtier (Fuchs, Habicht …)")),
    "abgegeben": (-1, N_("abgegeben")),
    "abgang": (-1, N_("sonstiger Abgang")),
}


class Household(db.Model):
    """Haushalt, an den Eier verteilt werden."""
    __tablename__ = "households"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    note = db.Column(db.String(200))
    active = db.Column(db.Boolean, nullable=False, default=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    @property
    def label(self):                             # fürs Änderungsprotokoll
        return self.name


class EggEntry(db.Model):
    """Eier eingesammelt (mehrere Einträge am Tag möglich), davon kaputt/schmutzig."""
    __tablename__ = "egg_entries"

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    eggs = db.Column(db.Integer, nullable=False)
    cracked = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    note = db.Column(db.String(200))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User")

    @property
    def label(self):
        return f"{self.day.strftime('%d.%m.%Y')}: {self.eggs}"


class EggShare(db.Model):
    """Eier an einen Haushalt gegeben."""
    __tablename__ = "egg_shares"

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    household_id = db.Column(db.Integer, db.ForeignKey("households.id", ondelete="RESTRICT"), nullable=False,
                             index=True)
    eggs = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(200))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    household = db.relationship("Household")
    user = db.relationship("User")

    @property
    def label(self):
        return f"{self.day.strftime('%d.%m.%Y')}: {self.eggs} → {self.household.name if self.household else ''}"


class FlockEvent(db.Model):
    """Zu- oder Abgang in der Herde (`change` mit Vorzeichen)."""
    __tablename__ = "flock_events"

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    change = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(20), nullable=False)
    breed = db.Column(db.String(80))
    hatched_on = db.Column(db.Date)              # Schlupf (Alter der Gruppe)
    note = db.Column(db.String(200))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User")

    @property
    def reason_label(self):
        return _(FLOCK_REASONS[self.reason][1]) if self.reason in FLOCK_REASONS else self.reason

    @property
    def label(self):
        return f"{self.day.strftime('%d.%m.%Y')}: {self.change:+d}"


class FeedEntry(db.Model):
    """Futter gekauft bzw. geholt (kg, Kosten wahlweise)."""
    __tablename__ = "feed_entries"

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    kg = db.Column(db.Numeric(8, 1), nullable=False)
    cost = db.Column(db.Numeric(10, 2))
    product = db.Column(db.String(100))
    note = db.Column(db.String(200))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User")

    @property
    def label(self):
        return f"{self.day.strftime('%d.%m.%Y')}: {self.kg} kg"


class FlockTreatment(db.Model):
    """Stallbuch: Behandlung (Wurmkur, Impfung, Medikament) mit Wartezeit für die Eier."""
    __tablename__ = "flock_treatments"

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    product = db.Column(db.String(120), nullable=False)
    reason = db.Column(db.String(200))
    withdrawal_days = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    note = db.Column(db.String(300))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User")

    @property
    def blocked_until(self):
        """Letzter Tag, an dem die Eier nicht gegessen/abgegeben werden dürfen (None = keine Wartezeit)."""
        return self.day + timedelta(days=self.withdrawal_days) if self.withdrawal_days else None

    @property
    def label(self):
        return f"{self.day.strftime('%d.%m.%Y')}: {self.product}"


class CoopReading(db.Model):
    """Messwert vom Sensor am Hühnerstall (Meshtastic-Telemetrie über die Brücke, `coop.record`)."""
    __tablename__ = "coop_readings"

    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    node = db.Column(db.String(20))                     # Meshtastic-Knoten, z. B. !a1b2c3d4
    temperature = db.Column(db.Float)                  # °C im Stall
    humidity = db.Column(db.Float)                     # % relative Luftfeuchte
    battery = db.Column(db.Float)                      # % Akku des Knotens
    voltage = db.Column(db.Float)                      # V
    pressure = db.Column(db.Float)                     # hPa (BME280/BME680)
    gas = db.Column(db.Float)                          # Gas-Widerstand in MOhm (BME680): sinkt bei „dicker Luft“
    iaq = db.Column(db.Integer)                        # Luftqualität 0–500 (BME680, wenn die Firmware sie rechnet)
    rssi = db.Column(db.Float)
    snr = db.Column(db.Float)


class CoopDoorEvent(db.Model):
    """Klappe – jede Änderung eines Reedkontakts; Zustand = letzter Eintrag je Kontakt.
    `position` „unten“: is_open = Klappe nicht ganz unten. „oben“ (zweiter Kontakt, wahlweise): is_open = ganz oben."""
    __tablename__ = "coop_door_events"

    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    is_open = db.Column(db.Boolean, nullable=False)
    position = db.Column(db.String(10), nullable=False, default="unten", server_default="unten")
    node = db.Column(db.String(20))


class MeshNode(db.Model):
    """Meshtastic-Knoten, von dem die Brücke etwas geschickt hat (oder vom Admin vorab angelegt).
    `purpose` sagt, wohin seine Werte gehen: coop (Hühnerstall, Kontakt unten + Messwerte), coop_top (Kontakt oben),
    climate (Lagerklima → `ClimateSensor` mit gleichem `node`), ignore; leer = neu, noch nicht zugeordnet."""
    __tablename__ = "mesh_nodes"

    id = db.Column(db.Integer, primary_key=True)
    node = db.Column(db.String(20), nullable=False, unique=True)     # !a1b2c3d4
    name = db.Column(db.String(100))
    purpose = db.Column(db.String(20))
    last_seen = db.Column(db.DateTime(timezone=True))
    temperature = db.Column(db.Float)                  # letzte Werte (zum Erkennen neuer Knoten)
    humidity = db.Column(db.Float)
    co2 = db.Column(db.Integer)
    battery = db.Column(db.Float)
    voltage = db.Column(db.Float)
    rssi = db.Column(db.Float)
    snr = db.Column(db.Float)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    @property
    def label(self):
        return self.name or self.node


class ClimateSensor(db.Model):
    """Klimasensor im Lager (Meshtastic-Knoten mit SCD41 o. Ä.): einem Lager und wahlweise einer Reihe zugeordnet.
    Unbekannte Knoten legt die Schnittstelle ohne Lager an – sie erscheinen dann zum Zuordnen."""
    __tablename__ = "climate_sensors"

    id = db.Column(db.Integer, primary_key=True)
    node = db.Column(db.String(20), nullable=False, unique=True)     # !a1b2c3d4
    name = db.Column(db.String(100))
    warehouse_id = db.Column(db.Integer, db.ForeignKey("warehouses.id", ondelete="SET NULL"), index=True)
    row = db.Column(db.Integer)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    warehouse = db.relationship("Warehouse")

    @property
    def label(self):
        if self.name:
            return self.name
        if self.warehouse and self.row:
            return f"{self.warehouse.name} · {self.warehouse.row_label(self.row)}"
        return self.warehouse.name if self.warehouse else self.node


class ClimateReading(db.Model):
    """Messwert eines Klimasensors."""
    __tablename__ = "climate_readings"

    id = db.Column(db.Integer, primary_key=True)
    sensor_id = db.Column(db.Integer, db.ForeignKey("climate_sensors.id", ondelete="CASCADE"), nullable=False)
    at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    temperature = db.Column(db.Float)
    humidity = db.Column(db.Float)
    co2 = db.Column(db.Integer)                          # ppm
    battery = db.Column(db.Float)
    voltage = db.Column(db.Float)

    sensor = db.relationship("ClimateSensor")

    __table_args__ = (db.Index("ix_climate_readings_sensor_at", "sensor_id", "at"),)
