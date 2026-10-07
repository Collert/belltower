from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import List, Task


class TaskSerializer(serializers.ModelSerializer):
    subtasks = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Task
        fields = [
            'id', 'title', 'description', 'completed', 'completed_at', 'created_at', 'expires_at',
            'is_expired', 'list', 'parent', 'subtasks',
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

    def validate(self, attrs):
        parent = attrs.get('parent', getattr(self.instance, 'parent', None))
        task_list = attrs.get('list', getattr(self.instance, 'list', None))
        if parent is not None:
            if parent.list_id != task_list.id:
                raise serializers.ValidationError({'parent': 'Parent task must be in the same list.'})
            if self.instance is not None and parent.pk == self.instance.pk:
                raise serializers.ValidationError({'parent': 'A task cannot be its own parent.'})
        return attrs


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
    open_task_count = serializers.SerializerMethodField()

    class Meta:
        model = List
        fields = ['id', 'name', 'users', 'persistent', 'created_at', 'open_task_count']
        read_only_fields = ['created_at']

    def get_open_task_count(self, obj):
        return obj.tasks.active().count()

    def create(self, validated_data):
        task_list = super().create(validated_data)
        task_list.users.add(self.context['request'].user)
        return task_list
