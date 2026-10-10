from django.contrib import admin

from .models import List, Task


class TaskInline(admin.TabularInline):
    model = Task
    fields = ['title', 'completed', 'expires_at', 'parent']
    extra = 0


@admin.register(List)
class ListAdmin(admin.ModelAdmin):
    list_display = ['name', 'persistent', 'created_at', 'last_activity_at']
    list_filter = ['persistent']
    filter_horizontal = ['users', 'admins']
    inlines = [TaskInline]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'list', 'completed', 'created_at', 'expires_at']
    list_filter = ['completed', 'list']
    search_fields = ['title', 'description']
