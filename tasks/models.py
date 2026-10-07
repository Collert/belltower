from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class List(models.Model):
    name = models.CharField(max_length=200)
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='lists', blank=True)
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

    objects = TaskQuerySet.as_manager()

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return self.title

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def save(self, *args, **kwargs):
        if self.completed and self.completed_at is None:
            self.completed_at = timezone.now()
        elif not self.completed:
            self.completed_at = None
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'completed' in update_fields:
            kwargs['update_fields'] = {*update_fields, 'completed_at'}
        super().save(*args, **kwargs)
