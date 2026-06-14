#!/bin/sh
set -e
echo "Running migrations..."
python manage.py migrate --noinput --settings=config.settings.production
echo "Starting Gunicorn..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 4 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
