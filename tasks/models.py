from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class List(models.Model):
    """A shared to-do list. Members (`users`) see its tasks, complete them and take them
    on; admins (a subset of members) also add, edit and delete tasks, assign them to any
    member, and manage the list and its members. Whoever creates a list is its admin."""

    name = models.CharField(max_length=200)
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='lists', blank=True)
    admins = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='administered_lists', blank=True,
        help_text='Members who can add, edit and assign tasks. Always also members.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Bumped whenever a task is added to or removed from the list; the cleanup
    # job uses it to decide how long a list has been sitting empty.
    last_activity_at = models.DateTimeField(default=timezone.now)
    # Persistent lists are never cleaned up automatically: they stay when empty and keep
    # their completed and expired tasks (e.g. an event's planning checklist).
    persistent = models.BooleanField(default=False)

    class Meta:
        ordering = ['name', 'id']

    def __str__(self):
        return self.name

    def is_admin(self, user):
        return self.admins.filter(pk=user.pk).exists()

    def add_member(self, user, admin=False):
        """Add ``user`` (or change their role). ``admin=None`` keeps an existing member's role."""
        self.users.add(user)
        if admin:
            self.admins.add(user)
        elif admin is False:
            self.admins.remove(user)

    def remove_member(self, user):
        self.admins.remove(user)
        self.users.remove(user)
        self.tasks.filter(assignee=user).update(assignee=None)

    @transaction.atomic
    def merge(self, other, name=None):
        """Fold ``other`` into this list: its tasks, members and admins move here, the
        name becomes "This & Other" (unless ``name`` is given), and ``other`` is deleted."""
        if other.pk == self.pk:
            raise ValueError('A list cannot be merged into itself.')
        other.tasks.update(list=self)
        self.users.add(*other.users.all())
        self.admins.add(*other.admins.all())
        self.name = (name or f'{self.name} & {other.name}')[:200]
        self.persistent = self.persistent or other.persistent
        self.last_activity_at = timezone.now()
        self.save()
        other.delete()
        return self


class TaskQuerySet(models.QuerySet):
    def active(self):
        """Tasks that still need doing: not completed and not expired."""
        return self.filter(completed=False).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))


class Task(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    list = models.ForeignKey('List', on_delete=models.CASCADE, related_name='tasks')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subtasks')
    # Who's doing it. Admins assign anyone in the list; members can only take a task
    # themselves (or let go of one they hold).
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tasks',
    )

    objects = TaskQuerySet.as_manager()

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return self.title

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def descendants(self):
        """Subtasks at every depth."""
        found, level = [], list(self.subtasks.all())
        while level:
            found.extend(level)
            level = list(Task.objects.filter(parent__in=level))
        return found

    @transaction.atomic
    def move_to(self, task_list):
        """Move this task and its subtasks to another list. A subtask moved on its own
        leaves its parent behind; assignees who aren't members there are unassigned."""
        old_list_id = self.list_id
        if task_list.pk == old_list_id:
            return self
        family = Task.objects.filter(pk__in=[self.pk, *(t.pk for t in self.descendants())])
        family.update(list=task_list)
        family.exclude(assignee__in=task_list.users.all()).update(assignee=None)
        if self.parent_id:
            Task.objects.filter(pk=self.pk).update(parent=None)
        # Bulk updates skip the signal that tracks list activity (for empty-list cleanup).
        List.objects.filter(pk__in=[old_list_id, task_list.pk]).update(last_activity_at=timezone.now())
        self.refresh_from_db()
        return self

    def save(self, *args, **kwargs):
        if self.completed and self.completed_at is None:
            self.completed_at = timezone.now()
        elif not self.completed:
            self.completed_at = None
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'completed' in update_fields:
            kwargs['update_fields'] = {*update_fields, 'completed_at'}
        super().save(*args, **kwargs)
