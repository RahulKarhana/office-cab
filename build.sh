#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py shell -c "from django.contrib.auth import get_user_model; User=get_user_model(); username='admin'; email='admin@gmail.com'; password='admin123'; user, created = User.objects.get_or_create(username=username, defaults={'email': email}); user.email = email; user.is_staff = True; user.is_superuser = True; user.set_password(password); user.save(); print('ADMIN_READY')"