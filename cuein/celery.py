import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cuein.settings')

app = Celery('cuein')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
