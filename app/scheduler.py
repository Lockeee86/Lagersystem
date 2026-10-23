"""Zeitplan im Hintergrund: nächtliche Datensicherung und tägliche Push-Nachrichten.

Läuft als Thread im (einzigen) Gunicorn-Worker, gestartet aus `gunicorn.conf.py`.
Jede Aufgabe merkt sich selbst, ob sie heute schon dran war – ein Neustart schadet nicht.
"""
import threading
import time

CHECK_EVERY = 300                     # Sekunden zwischen zwei Prüfungen


def _jobs():
    from . import backup, push
    return [("Datensicherung", backup.tick), ("Push", push.daily)]


def start(app):
    if app.config.get("TESTING"):
        return None

    def loop():
        time.sleep(60)                # erst nach dem Start loslegen
        while True:
            for name, job in _jobs():
                try:
                    job(app)
                except Exception:     # noqa: BLE001 – der Thread darf nie sterben
                    app.logger.exception("Zeitplan: %s", name)
            time.sleep(CHECK_EVERY)
    thread = threading.Thread(target=loop, name="scheduler", daemon=True)
    thread.start()
    return thread
