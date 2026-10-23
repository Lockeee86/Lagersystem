# Wird von Gunicorn automatisch geladen (Arbeitsverzeichnis /app).
def post_worker_init(worker):
    """Nächtliche Datensicherung im (einzigen) Worker starten."""
    from app.backup import start_scheduler
    start_scheduler(worker.wsgi)
