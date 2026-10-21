"""Kommandos für die Konsole, z. B. in Portainer über "Console" im web-Container:

    flask create-admin <benutzername>
    flask reset-password <benutzername>
"""
import click

from .extensions import db
from .models import ROLE_ADMIN, User


def register(app):
    @app.cli.command("create-admin")
    @click.argument("username")
    @click.password_option()
    def create_admin(username, password):
        """Legt einen Admin an (oder macht einen bestehenden Benutzer zum Admin)."""
        username = username.lower()
        user = User.query.filter_by(username=username).first() or User(username=username)
        user.role = ROLE_ADMIN
        user.active = True
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin '{username}' ist eingerichtet.")

    @app.cli.command("reset-password")
    @click.argument("username")
    @click.password_option()
    def reset_password(username, password):
        """Setzt das Passwort eines Benutzers neu."""
        user = User.query.filter_by(username=username.lower()).first()
        if not user:
            raise click.ClickException(f"Benutzer '{username}' nicht gefunden.")
        user.set_password(password)
        db.session.commit()
        click.echo(f"Passwort für '{user.username}' wurde geändert.")
