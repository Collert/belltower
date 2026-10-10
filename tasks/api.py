from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import List, Task
from .permissions import require_admin
from .serializers import ListSerializer, MemberSerializer, TaskSerializer, UserSerializer


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
    """Lists you're a member of. Changing or deleting a list, and managing its members,
    is for its admins.

    `POST /api/lists/<id>/members/` with `email` (or `username`) and `admin` adds someone or
    changes their role (leave `admin` out to add without changing an existing member's role); `DELETE /api/lists/<id>/members/<username>/` removes them. Both
    return the updated list. An email no account uses gives 404.

    `POST /api/lists/<id>/merge/` with `list` (another list's id, and optionally `name`)
    moves that list's tasks, members and admins into this one, names it "This & Other",
    deletes the other list and returns the merged one. You must be an admin of both.
    """

    serializer_class = ListSerializer

    def get_queryset(self):
        return List.objects.filter(users=self.request.user).prefetch_related('users', 'admins')

    def perform_destroy(self, instance):
        require_admin(self.request.user, instance, 'delete the list')
        instance.delete()

    def _list_response(self, task_list):
        task_list = self.get_queryset().get(pk=task_list.pk)  # fresh prefetch
        return Response(self.get_serializer(task_list).data)

    @action(detail=True, methods=['post'])
    def members(self, request, pk=None):
        task_list = self.get_object()
        require_admin(request.user, task_list, 'manage members')
        serializer = MemberSerializer(data=request.data)
        if not serializer.is_valid():
            no_user = any(getattr(e, 'code', '') == 'no_user' for errors in serializer.errors.values() for e in errors)
            return Response(serializer.errors, status=status.HTTP_404_NOT_FOUND if no_user else status.HTTP_400_BAD_REQUEST)
        user, admin = serializer.validated_data['user'], serializer.validated_data['admin']
        if user == request.user and admin is False:
            raise ValidationError("You can't remove your own admin role.")
        task_list.add_member(user, admin=admin)
        return self._list_response(task_list)

    @action(detail=True, methods=['post'])
    def merge(self, request, pk=None):
        task_list = self.get_object()
        other_id = str(request.data.get('list', ''))
        if not other_id.isdigit() or int(other_id) == task_list.pk:
            raise ValidationError({'list': 'Give the id of another list to merge into this one.'})
        other = get_object_or_404(self.get_queryset(), pk=other_id)
        require_admin(request.user, task_list, 'merge lists')
        require_admin(request.user, other, 'merge lists')
        task_list.merge(other, name=(request.data.get('name') or '').strip() or None)
        return self._list_response(task_list)

    @action(detail=True, methods=['delete'], url_path=r'members/(?P<username>[^/]+)')
    def remove_member(self, request, pk=None, username=None):
        task_list = self.get_object()
        require_admin(request.user, task_list, 'manage members')
        user = get_object_or_404(task_list.users, username=username)
        if user == request.user:
            raise ValidationError("You can't remove yourself from a list you manage.")
        task_list.remove_member(user)
        return self._list_response(task_list)


class TaskViewSet(viewsets.ModelViewSet):
    """Tasks in lists you belong to. Admins of a list add, edit and delete its tasks;
    members can complete tasks and take them on (`assignee` = themselves).

    Filters: `?list=<id>`, `?completed=true|false`, `?active=true` (not completed, not expired),
    `?since=<ISO datetime>` (created after; lets devices poll for new tasks),
    `?assignee=<username>|me|none`.
    """

    serializer_class = TaskSerializer

    def get_queryset(self):
        queryset = Task.objects.filter(list__users=self.request.user).select_related('assignee').prefetch_related('subtasks')
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
        if 'assignee' in params:
            assignee = params['assignee']
            if assignee == 'none':
                queryset = queryset.filter(assignee__isnull=True)
            elif assignee == 'me':
                queryset = queryset.filter(assignee=self.request.user)
            else:
                queryset = queryset.filter(assignee__username=assignee)
        return queryset

    def perform_destroy(self, instance):
        require_admin(self.request.user, instance.list, 'delete tasks')
        instance.delete()
