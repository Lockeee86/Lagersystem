#!/bin/sh
set -e
# Datenbank-Schema auf den neuesten Stand bringen (legt beim ersten Start alle Tabellen an)
flask --app wsgi db upgrade
exec "$@"
