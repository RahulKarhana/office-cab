#!/usr/bin/env bash

set -o errexit

pip install -r requirements.txt

python manage.py migrate

echo "===== RESETTING RENDER DATABASE ====="

python manage.py flush --noinput

echo "===== DATABASE RESET COMPLETE ====="

python manage.py shell -c "import os; from django.contrib.auth import get_user_model; User=get_user_model(); username=os.environ['DJANGO_SUPERUSER_USERNAME']; email=os.environ.get('DJANGO_SUPERUSER_EMAIL',''); password=os.environ['DJANGO_SUPERUSER_PASSWORD']; u,created=User.objects.get_or_create(username=username, defaults={'email':email, 'is_staff':True, 'is_superuser':True, 'is_active':True, 'role':'ADMIN'}); u.email=email; u.is_staff=True; u.is_superuser=True; u.is_active=True; u.role='ADMIN'; u.set_password(password); u.save(); print('ADMIN READY:', u.username); print('TOTAL USERS AFTER RESET:', User.objects.count())"

python manage.py collectstatic --noinput