#!/usr/bin/env bash

set -o errexit

pip install -r requirements.txt

python manage.py migrate

# ONE-TIME RESET OF RENDER DATABASE
python manage.py flush --noinput

# Recreate Render admin using existing environment variables
python manage.py createsuperuser --noinput

python manage.py collectstatic --noinput