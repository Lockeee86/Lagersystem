import pytest
from flask import g

from app import create_app
from app.extensions import db as _db
from app.models import BoxType, Plot, User, Variety, Warehouse


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test",
    })

    @app.before_request
    def _fresh_login():
        # Der Test hält einen App-Kontext offen → `g` würde sonst den Benutzer
        # der vorherigen Anfrage (evtl. eines anderen Test-Clients) wiederverwenden.
        g.pop("_login_user", None)
        g.pop("farm", None)
        g.pop("my_tasks", None)
        g.pop("_lang", None)
        g.pop("purchase_count", None)

    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def db(app):
    return _db


def make_user(username, role, password="geheim123"):
    user = User(username=username, role=role)
    user.set_password(password)
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture
def seed(app):
    admin = make_user("admin", "admin")
    worker = make_user("max", "mitarbeiter")
    viewer = make_user("oma", "betrachter")
    wh = Warehouse(name="Kartoffellager", rows=10, slots=15, max_levels=5)
    bt = BoxType(name="Standardkiste", full_weight_kg=400)
    belana = Variety(name="Belana", color="#e6b422")
    plot = Plot(name="Acker Nord")
    _db.session.add_all([wh, bt, belana, plot])
    _db.session.commit()
    return {"admin": admin, "worker": worker, "viewer": viewer, "wh": wh, "bt": bt, "belana": belana, "plot": plot}


def login(client, username, password="geheim123"):
    return client.post("/login", data={"username": username, "password": password})


@pytest.fixture
def as_admin(app, seed):
    client = app.test_client()
    login(client, "admin")
    return client


@pytest.fixture
def as_worker(app, seed):
    client = app.test_client()
    login(client, "max")
    return client


@pytest.fixture
def as_viewer(app, seed):
    client = app.test_client()
    login(client, "oma")
    return client
