from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import List, Task
from .permissions import check_task_update, require_admin


def _request_user(serializer):
    return serializer.context['request'].user


class TaskSerializer(serializers.ModelSerializer):
    subtasks = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    assignee = serializers.SlugRelatedField(
        slug_field='username', queryset=get_user_model().objects.all(), required=False, allow_null=True,
        help_text='Username of the member doing it. Members can only assign themselves.',
    )
    assignee_name = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'title', 'description', 'completed', 'completed_at', 'created_at', 'expires_at',
            'is_expired', 'list', 'parent', 'subtasks', 'assignee', 'assignee_name',
        ]
        read_only_fields = ['completed_at', 'created_at']

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            # Only allow attaching tasks to lists the user belongs to.
            fields['list'].queryset = List.objects.filter(users=request.user)
            fields['parent'].queryset = Task.objects.filter(list__users=request.user)
        return fields

    def get_assignee_name(self, obj):
        return (obj.assignee.get_full_name() or obj.assignee.get_username()) if obj.assignee else ''

    def validate(self, attrs):
        task_list = attrs.get('list', getattr(self.instance, 'list', None))
        moving = self.instance is not None and task_list != self.instance.list
        # A task moved to another list leaves its parent behind unless a new one is given.
        parent = attrs.get('parent', None if moving else getattr(self.instance, 'parent', None))
        if parent is not None:
            if parent.list_id != task_list.id:
                raise serializers.ValidationError({'parent': 'Parent task must be in the same list.'})
            if self.instance is not None and parent.pk == self.instance.pk:
                raise serializers.ValidationError({'parent': 'A task cannot be its own parent.'})
        assignee = attrs.get('assignee')
        if assignee is not None and not task_list.users.filter(pk=assignee.pk).exists():
            raise serializers.ValidationError({'assignee': 'Tasks can only be assigned to members of the list.'})
        return attrs

    def create(self, validated_data):
        require_admin(_request_user(self), validated_data['list'], 'add tasks')
        return super().create(validated_data)

    def update(self, instance, validated_data):
        user = _request_user(self)
        new_list = validated_data.pop('list', instance.list)
        if new_list != instance.list:
            require_admin(user, instance.list, 'move tasks')
            require_admin(user, new_list, 'add tasks')
            instance.move_to(new_list)  # brings its subtasks along
        check_task_update(user, instance, validated_data)
        return super().update(instance, validated_data)


class UserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ['username', 'name']

    def get_name(self, obj):
        return obj.get_full_name()


class ListSerializer(serializers.ModelSerializer):
    users = serializers.SlugRelatedField(
        many=True, required=False, slug_field='username', queryset=get_user_model().objects.all(),
        help_text='Usernames of members. The creator is always added.',
    )
    admins = serializers.SlugRelatedField(
        many=True, required=False, slug_field='username', queryset=get_user_model().objects.all(),
        help_text='Usernames of members who can add, edit and assign tasks. They are added as members too.',
    )
    members = serializers.SerializerMethodField(help_text='Everyone in the list, with their email and role.')
    is_admin = serializers.SerializerMethodField(help_text='Whether you are an admin of this list.')
    open_task_count = serializers.SerializerMethodField()

    class Meta:
        model = List
        fields = ['id', 'name', 'users', 'admins', 'members', 'is_admin', 'persistent', 'created_at', 'open_task_count']
        read_only_fields = ['created_at']

    def get_members(self, obj):
        admin_ids = {u.pk for u in obj.admins.all()}
        return [
            {'username': u.get_username(), 'name': u.get_full_name(), 'email': u.email, 'admin': u.pk in admin_ids}
            for u in obj.users.all()
        ]

    def get_is_admin(self, obj):
        return any(u.pk == _request_user(self).pk for u in obj.admins.all())

    def get_open_task_count(self, obj):
        return obj.tasks.active().count()

    def create(self, validated_data):
        admins = validated_data.pop('admins', [])
        task_list = super().create(validated_data)
        task_list.users.add(_request_user(self), *admins)
        task_list.admins.add(_request_user(self), *admins)
        return task_list

    def update(self, instance, validated_data):
        require_admin(_request_user(self), instance, 'change the list')
        admins = validated_data.pop('admins', None)
        task_list = super().update(instance, validated_data)
        if admins is not None:
            task_list.admins.set(admins)
        # Admins are always members; anyone no longer a member drops their admin role.
        task_list.users.add(*task_list.admins.all())
        task_list.admins.remove(*task_list.admins.exclude(pk__in=task_list.users.all()))
        task_list.tasks.exclude(assignee__isnull=True).exclude(assignee__in=task_list.users.all()).update(assignee=None)
        return task_list


class MemberSerializer(serializers.Serializer):
    """Add someone to a list (or change their role) by email or username."""

    email = serializers.EmailField(required=False)
    username = serializers.CharField(required=False)
    # Omitted: a new member joins as a plain member and an existing one keeps their role.
    admin = serializers.BooleanField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        users = get_user_model().objects.filter(is_active=True)
        if attrs.get('email'):
            matches = list(users.filter(email__iexact=attrs['email'])[:2])
            if len(matches) > 1:
                raise serializers.ValidationError({'email': 'More than one Belltower account uses that email.'})
        elif attrs.get('username'):
            matches = list(users.filter(username=attrs['username']))
        else:
            raise serializers.ValidationError('Give an email or a username.')
        if not matches:
            raise serializers.ValidationError({'email' if attrs.get('email') else 'username': 'No Belltower account matches.'}, code='no_user')
        attrs['user'] = matches[0]
        return attrs
