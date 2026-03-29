#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py shell -c "from django.contrib.auth import get_user_model; User=get_user_model(); username='admin'; password='admin123'; email='admin@gmail.com'; 
if not User.objects.filter(username=username).exists(): 
    User.objects.create_superuser(username=username, email=email, password=password)"