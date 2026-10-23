"""Kommandos für die Konsole, z. B. in Portainer über "Console" im web-Container:

    flask create-admin <benutzername>
    flask reset-password <benutzername>
    flask backup-now
    flask backup-restore /backup/db/lager-2026-10-23_0300.dump
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

    @app.cli.command("backup-now")
    def backup_now():
        """Sofort eine Datensicherung nach BACKUP_DIR schreiben."""
        from . import backup
        try:
            status = backup.run_backup()
        except backup.BackupError as e:
            raise click.ClickException(str(e))
        if not status.get("ok"):
            raise click.ClickException(f"Sicherung fehlgeschlagen: {status.get('error')}")
        click.echo(f"Gesichert: {status['file']} ({status['size']} Bytes), {status['files_copied']} neue Dateien.")

    @app.cli.command("backup-restore")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    @click.confirmation_option(prompt="Die aktuelle Datenbank wird komplett ersetzt. Wirklich zurückspielen?")
    def backup_restore(path):
        """Einen Datenbank-Abzug zurückspielen (ersetzt alle Daten!). Fotos: Ordner dateien/ zurückkopieren."""
        from . import backup
        db.session.remove()
        db.engine.dispose()
        try:
            backup.restore(path)
        except backup.BackupError as e:
            raise click.ClickException(str(e))
        click.echo("Zurückgespielt. Danach den web-Container neu starten.")
