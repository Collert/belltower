"""Tools exposed to AI agents over MCP.

Each tool runs as the authenticated user and goes through the same serializers as the
REST API, so validation and list-membership rules are identical.
"""

from dataclasses import dataclass
from typing import Callable

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from tasks.models import List, Task
from tasks.permissions import check_task_update, require_admin
from tasks.serializers import ListSerializer, TaskSerializer


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable

    def definition(self):
        return {'name': self.name, 'description': self.description, 'inputSchema': self.input_schema}


TOOLS = {}

ID = {'type': 'integer'}
DATETIME = {'type': ['string', 'null'], 'format': 'date-time', 'description': 'ISO 8601, e.g. 2026-10-06T18:00:00Z'}


def tool(name, description, properties=None, required=()):
    def register(handler):
        schema = {'type': 'object', 'properties': properties or {}, 'required': list(required)}
        TOOLS[name] = Tool(name, description, schema, handler)
        return handler
    return register


def _lists(request):
    return List.objects.filter(users=request.user)


def _tasks(request):
    return Task.objects.filter(list__users=request.user)


def _save(serializer):
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return serializer.data


@tool('list_lists', 'List the task lists you belong to, with their count of open tasks.')
def list_lists(request):
    return ListSerializer(_lists(request), many=True, context={'request': request}).data


@tool(
    'create_list', 'Create a new task list. You are added as a member automatically.',
    {
        'name': {'type': 'string'},
        'users': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Usernames of other members.'},
    },
    required=['name'],
)
def create_list(request, name, users=()):
    return _save(ListSerializer(data={'name': name, 'users': list(users)}, context={'request': request}))


@tool('rename_list', 'Rename a task list.', {'list_id': ID, 'name': {'type': 'string'}}, required=['list_id', 'name'])
def rename_list(request, list_id, name):
    task_list = get_object_or_404(_lists(request), pk=list_id)
    return _save(ListSerializer(task_list, data={'name': name}, partial=True, context={'request': request}))


@tool(
    'merge_lists', 'Merge one list into another: its tasks, members and admins move over, the name becomes '
    '"Target & Source", and the source list is deleted. You must be an admin of both.',
    {'target_list_id': ID, 'source_list_id': ID}, required=['target_list_id', 'source_list_id'],
)
def merge_lists(request, target_list_id, source_list_id):
    target = get_object_or_404(_lists(request), pk=target_list_id)
    source = get_object_or_404(_lists(request).exclude(pk=target.pk), pk=source_list_id)
    require_admin(request.user, target, 'merge lists')
    require_admin(request.user, source, 'merge lists')
    target.merge(source)
    return ListSerializer(target, context={'request': request}).data


@tool(
    'move_task', 'Move a task (and its subtasks) to another list. You must be an admin of both lists.',
    {'task_id': ID, 'list_id': ID}, required=['task_id', 'list_id'],
)
def move_task(request, task_id, list_id):
    task = get_object_or_404(_tasks(request), pk=task_id)
    return _save(TaskSerializer(task, data={'list': list_id}, partial=True, context={'request': request}))


@tool('delete_list', 'Delete a task list and all of its tasks.', {'list_id': ID}, required=['list_id'])
def delete_list(request, list_id):
    task_list = get_object_or_404(_lists(request), pk=list_id)
    require_admin(request.user, task_list, 'delete the list')
    task_list.delete()
    return {'deleted': True}


@tool(
    'list_tasks', 'List tasks, optionally in one list. By default only open (not completed, not expired) tasks.',
    {
        'list_id': ID,
        'include_completed': {'type': 'boolean', 'default': False},
        'include_expired': {'type': 'boolean', 'default': False},
    },
)
def list_tasks(request, list_id=None, include_completed=False, include_expired=False):
    tasks = _tasks(request).prefetch_related('subtasks')
    if list_id is not None:
        tasks = tasks.filter(list_id=list_id)
    if not include_completed:
        tasks = tasks.filter(completed=False)
    if not include_expired:
        tasks = tasks.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    return TaskSerializer(tasks, many=True, context={'request': request}).data


@tool(
    'create_task', 'Add a task to a list.',
    {
        'list_id': ID,
        'title': {'type': 'string'},
        'description': {'type': 'string'},
        'expires_at': DATETIME,
        'parent_id': {**ID, 'description': 'Make this a subtask of another task in the same list.'},
        'assignee': {'type': ['string', 'null'], 'description': 'Username of the list member doing it.'},
    },
    required=['list_id', 'title'],
)
def create_task(request, list_id, title, description='', expires_at=None, parent_id=None, assignee=None):
    data = {
        'list': list_id, 'title': title, 'description': description, 'expires_at': expires_at,
        'parent': parent_id, 'assignee': assignee,
    }
    return _save(TaskSerializer(data=data, context={'request': request}))


@tool(
    'update_task', 'Change a task. Only the fields you pass are updated; pass expires_at=null to clear it.',
    {
        'task_id': ID,
        'title': {'type': 'string'},
        'description': {'type': 'string'},
        'expires_at': DATETIME,
        'completed': {'type': 'boolean'},
        'assignee': {'type': ['string', 'null'], 'description': 'Username of the list member doing it; null to unassign.'},
    },
    required=['task_id'],
)
def update_task(request, task_id, **changes):
    unknown = changes.keys() - {'title', 'description', 'expires_at', 'completed', 'assignee'}
    if unknown:
        raise TypeError(f"unexpected argument(s): {', '.join(sorted(unknown))}")
    task = get_object_or_404(_tasks(request), pk=task_id)
    return _save(TaskSerializer(task, data=changes, partial=True, context={'request': request}))


@tool('complete_task', 'Mark a task as done.', {'task_id': ID}, required=['task_id'])
def complete_task(request, task_id):
    return update_task(request, task_id, completed=True)


@tool('take_task', 'Assign a task to yourself, or let go of it with release=true.',
      {'task_id': ID, 'release': {'type': 'boolean', 'default': False}}, required=['task_id'])
def take_task(request, task_id, release=False):
    task = get_object_or_404(_tasks(request), pk=task_id)
    assignee = None if release else request.user
    check_task_update(request.user, task, {'assignee': assignee})
    task.assignee = assignee
    task.save(update_fields=['assignee'])
    return TaskSerializer(task, context={'request': request}).data


@tool('delete_task', 'Delete a task and its subtasks.', {'task_id': ID}, required=['task_id'])
def delete_task(request, task_id):
    task = get_object_or_404(_tasks(request), pk=task_id)
    require_admin(request.user, task.list, 'delete tasks')
    task.delete()
    return {'deleted': True}
