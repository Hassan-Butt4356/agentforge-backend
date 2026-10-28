"""
Celery application configuration for AgentForge.

This module is imported by config/__init__.py so that the Celery app
is always initialised when Django starts — ensuring @shared_task
decorators in any app are registered correctly.
"""
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("agentforge")

# Read all CELERY_* settings from Django settings automatically.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every installed app.
app.autodiscover_tasks()

app.conf.imports = (
    "rag.tasks.ingestion_tasks",
)

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
