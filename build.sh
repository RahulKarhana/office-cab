#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput

# Create or update admin safely
python manage.py shell -c "from django.contrib.auth import get_user_model; User=get_user_model(); user, created = User.objects.get_or_create(username='admin'); user.set_password('admin123'); user.is_staff=True; user.is_superuser=True; user.save(); print('ADMIN_READY')"