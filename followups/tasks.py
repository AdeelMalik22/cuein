from celery import shared_task

from .services import flag_overdue_tasks as flag_overdue_tasks_service
from .services import schedule_rule, schedule_stale_escalations as schedule_stale_escalations_service


@shared_task
def schedule_follow_up(business_id, lead_id, rule_key):
    task, created = schedule_rule(business_id=business_id, lead_id=lead_id, rule_key=rule_key)
    return {'task_id': str(task.id), 'created': created}


@shared_task
def flag_overdue_tasks():
    return flag_overdue_tasks_service()


@shared_task
def schedule_stale_lead_escalations():
    return schedule_stale_escalations_service()
