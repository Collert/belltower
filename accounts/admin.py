from django.contrib import admin

from .models import ApiKey, SsoIdentity


@admin.register(SsoIdentity)
class SsoIdentityAdmin(admin.ModelAdmin):
    list_display = ['user', 'provider', 'subject', 'created_at']
    search_fields = ['user__username', 'subject']


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'prefix', 'created_at', 'last_used_at']
    search_fields = ['name', 'user__username']
    readonly_fields = ['prefix', 'created_at', 'last_used_at']

    def has_add_permission(self, request):
        return False  # keys are issued through /connect/, which shows the secret once
