from datetime import timedelta

from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import ApiKey


class BearerTokenAuthentication(TokenAuthentication):
    """DRF token auth using the standard `Authorization: Bearer <token>` header.

    This is the header MCP clients send. Accepts both personal tokens (account page,
    /api/auth/token/) and API keys issued to connected apps (/connect/); for the latter
    `request.auth` is the ApiKey, so the app can revoke itself.
    """

    keyword = 'Bearer'

    def authenticate_credentials(self, key):
        api_key = ApiKey.objects.by_secret(key)
        if api_key is None:
            return super().authenticate_credentials(key)
        if not api_key.user.is_active:
            raise AuthenticationFailed('User inactive or deleted.')
        now = timezone.now()
        if api_key.last_used_at is None or now - api_key.last_used_at > timedelta(minutes=5):
            ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=now)  # throttled: no write per request
        return api_key.user, api_key
