# Wird von Gunicorn automatisch geladen (Arbeitsverzeichnis /app).
def post_worker_init(worker):
    """Zeitplan (Datensicherung, tägliche Push-Nachrichten) im (einzigen) Worker starten."""
    from app import scheduler
    scheduler.start(worker.wsgi)
