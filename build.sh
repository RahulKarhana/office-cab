#!/usr/bin/env bash

set -o errexit

pip install -r requirements.txt

python manage.py migrate

python manage.py shell -c "from django.db import connection; from django.contrib.auth import get_user_model; User=get_user_model(); print('DATABASE ENGINE:', connection.settings_dict['ENGINE']); print('DATABASE HOST:', connection.settings_dict.get('HOST')); print('TOTAL USERS BEFORE RESET:', User.objects.count()); print('EMPLOYEES:', User.objects.filter(role='EMPLOYEE').count()); print('DRIVERS:', User.objects.filter(role='DRIVER').count())"

python manage.py collectstatic --noinput