FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# pg_dump/pg_restore für die Datensicherung – passend zur Datenbank (postgres:16) aus dem PostgreSQL-Repository;
# klappt das nicht, reicht auch der neuere Client aus Debian (kann ältere Server sichern)
ARG PG_MAJOR=16
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-common ca-certificates \
    && ( /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
         && apt-get install -y --no-install-recommends postgresql-client-${PG_MAJOR} \
         || apt-get install -y --no-install-recommends postgresql-client ) \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 60 -r requirements.txt

COPY . .

# /app/data: Volume für Fotos, /backup: Volume für die Datensicherung
# (gehören dem App-Benutzer, damit leere Volumes beschreibbar sind)
RUN useradd --system --uid 1000 lager && mkdir -p /app/data/uploads /backup && chown -R lager /app /backup
USER lager

EXPOSE 5000

ENTRYPOINT ["/app/entrypoint.sh"]
# Ein Worker mit Threads: die Login-Sperre liegt im Speicher und gilt so für alle Anfragen
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "60", "--access-logfile", "-", "wsgi:app"]
