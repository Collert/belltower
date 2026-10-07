from django.contrib.auth import get_user_model
from django.utils.dateparse import parse_datetime
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from .models import List, Task
from .serializers import ListSerializer, TaskSerializer, UserSerializer


def _flag(value):
    return value.lower() in ('1', 'true', 'yes')


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """Active users, so apps can offer people to share lists with. `?search=` filters by username."""

    serializer_class = UserSerializer
    lookup_field = 'username'

    def get_queryset(self):
        queryset = get_user_model().objects.filter(is_active=True).order_by('username')
        search = self.request.query_params.get('search', '').strip()
        return queryset.filter(username__icontains=search) if search else queryset


class ListViewSet(viewsets.ModelViewSet):
    serializer_class = ListSerializer

    def get_queryset(self):
        return List.objects.filter(users=self.request.user).prefetch_related('users')


class TaskViewSet(viewsets.ModelViewSet):
    """Tasks in lists you belong to.

    Filters: `?list=<id>`, `?completed=true|false`, `?active=true` (not completed, not expired),
    `?since=<ISO datetime>` (created after; lets devices poll for new tasks).
    """

    serializer_class = TaskSerializer

    def get_queryset(self):
        queryset = Task.objects.filter(list__users=self.request.user).prefetch_related('subtasks')
        params = self.request.query_params
        if 'list' in params:
            if not params['list'].isdigit():
                raise ValidationError({'list': 'Expected a list id.'})
            queryset = queryset.filter(list_id=params['list'])
        if 'completed' in params:
            queryset = queryset.filter(completed=_flag(params['completed']))
        if _flag(params.get('active', '')):
            queryset = queryset.active()
        if 'since' in params:
            since = parse_datetime(params['since'])
            if since is None:
                raise ValidationError({'since': 'Expected an ISO 8601 datetime.'})
            queryset = queryset.filter(created_at__gt=since)
        return queryset
