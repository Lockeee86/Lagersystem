FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 60 -r requirements.txt

COPY . .

RUN useradd --system --uid 1000 lager && chown -R lager /app
USER lager

EXPOSE 5000

ENTRYPOINT ["/app/entrypoint.sh"]
# Ein Worker mit Threads: die Login-Sperre liegt im Speicher und gilt so für alle Anfragen
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "60", "--access-logfile", "-", "wsgi:app"]
