"""Who may do what in a list. Shared by the REST API, the web UI and MCP tools.

- Members see the list's tasks, complete them, and take a task on (or let go of one
  they hold).
- Admins also rename/delete the list, manage members, add/edit/delete tasks and
  assign tasks to any member.

Every check raises NotAllowed, which the API turns into a 403 with the reason, the web
UI into a 403 page and MCP into a tool error.
"""
from django.core import exceptions as django_exceptions
from rest_framework import exceptions as drf_exceptions

# Fields a member who isn't an admin may change on a task.
MEMBER_TASK_FIELDS = {'completed', 'assignee'}


class NotAllowed(drf_exceptions.PermissionDenied, django_exceptions.PermissionDenied):
    """A 403 both DRF and plain Django views understand."""

    def __init__(self, detail):
        super().__init__(detail)
        self.args = (detail,)  # DRF rebuilds Django PermissionDenied from args


def require_admin(user, task_list, action='do that'):
    if not task_list.is_admin(user):
        raise NotAllowed(f'Only admins of "{task_list.name}" can {action}.')


def check_task_update(user, task, changes):
    """``changes`` maps field names to their new values (only what's being set)."""
    if task.list.is_admin(user):
        return
    changed = {name for name, value in changes.items() if getattr(task, name) != value}
    if changed - MEMBER_TASK_FIELDS:
        raise NotAllowed(f'Only admins of "{task.list.name}" can edit tasks. Members can complete tasks and take them on.')
    if 'assignee' in changed:
        new = changes['assignee']
        if new is not None and new != user:
            raise NotAllowed('You can only assign tasks to yourself.')
        if task.assignee is not None and task.assignee != user:
            raise NotAllowed(f'This task is assigned to {task.assignee.get_username()}.')
