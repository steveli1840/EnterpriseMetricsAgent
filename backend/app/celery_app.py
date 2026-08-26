from celery import Celery

from app.settings import get_settings

settings = get_settings()
celery_app = Celery("metrics_agent", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])
celery_app.autodiscover_tasks(["app.tasks"])

