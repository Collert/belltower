from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import List, Task


def _delete(queryset, label):
    # Re-select by pk: Django can't delete straight from querysets with joins/excludes like these.
    model = queryset.model
    _, per_model = model.objects.filter(pk__in=list(queryset.values_list('pk', flat=True))).delete()
    return per_model.get(label, 0)


@transaction.atomic
def run_cleanup(now=None):
    """Delete old completed tasks, expired tasks and lists that have been empty too long.

    Returns how many of each were deleted. Retention periods come from settings.BELLTOWER;
    a period of None disables that step. Persistent lists and their tasks are left alone.
    """
    now = now or timezone.now()
    config = settings.BELLTOWER
    deleted = {'completed_tasks': 0, 'expired_tasks': 0, 'empty_lists': 0}

    if config['COMPLETED_TASK_RETENTION'] is not None:
        old_completed = Task.objects.filter(
            list__persistent=False, completed=True, completed_at__lt=now - config['COMPLETED_TASK_RETENTION']
        ).exclude(subtasks__completed=False)  # keep parents that still have open subtasks
        deleted['completed_tasks'] = _delete(old_completed, 'tasks.Task')

    if config['EXPIRED_TASK_RETENTION'] is not None:
        expired = Task.objects.filter(list__persistent=False, expires_at__lt=now - config['EXPIRED_TASK_RETENTION'])
        deleted['expired_tasks'] = _delete(expired, 'tasks.Task')

    if config['EMPTY_LIST_RETENTION'] is not None:
        empty = List.objects.filter(persistent=False, tasks__isnull=True, last_activity_at__lt=now - config['EMPTY_LIST_RETENTION'])
        deleted['empty_lists'] = _delete(empty, 'tasks.List')

    return deleted
