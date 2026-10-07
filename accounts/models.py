import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class SsoIdentity(models.Model):
    """Links a local user to an account at an external identity provider (e.g. Authentik).

    Users are matched on the provider's stable `sub` claim rather than on username or
    email, so renaming someone in the identity provider doesn't orphan their account.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sso_identities')
    provider = models.CharField(max_length=50)
    subject = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'SSO identities'
        constraints = [
            models.UniqueConstraint(fields=['provider', 'subject'], name='unique_sso_identity'),
        ]

    def __str__(self):
        return f'{self.user} @ {self.provider}'


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


class ApiKeyQuerySet(models.QuerySet):
    def by_secret(self, secret):
        return self.select_related('user').filter(key_hash=_hash(secret)).first()


class ApiKey(models.Model):
    """A long-lived API key issued to an app that connected through /connect/.

    Unlike the personal DRF token (one per user, shown on the account page), a user can
    have one key per connected app, and revoking one doesn't break the others. Only a
    hash is stored; the key itself is shown to the app once, when it's issued.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_keys')
    name = models.CharField(max_length=200, help_text='The app it was issued to, e.g. "Town Hall (hall.example.org)"')
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    prefix = models.CharField(max_length=8, editable=False, help_text='First characters, to tell keys apart')
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = ApiKeyQuerySet.as_manager()

    class Meta:
        verbose_name = 'API key'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.prefix}…)'

    @classmethod
    def issue(cls, user, name):
        """Create a key and return (api_key, secret). The secret can't be recovered later."""
        secret = 'bt_' + secrets.token_urlsafe(32)
        return cls.objects.create(user=user, name=name, key_hash=_hash(secret), prefix=secret[:8]), secret


class ConnectGrant(models.Model):
    """A one-time code handed to an app at the end of /connect/authorize/.

    The app trades it (with the same redirect URI) for an ApiKey at /connect/token/.
    Codes expire after GRANT_LIFETIME and work once.
    """

    GRANT_LIFETIME = timedelta(minutes=5)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    client_name = models.CharField(max_length=200)
    redirect_uri = models.URLField(max_length=1000)
    code_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, user, client_name, redirect_uri):
        code = secrets.token_urlsafe(32)
        cls.objects.create(user=user, client_name=client_name, redirect_uri=redirect_uri, code_hash=_hash(code))
        return code

    @classmethod
    def redeem(cls, code, redirect_uri):
        """Consume a code. Returns the grant, or None if it's unknown, expired or for another redirect URI."""
        cls.objects.filter(created_at__lt=timezone.now() - cls.GRANT_LIFETIME).delete()
        grant = cls.objects.select_related('user').filter(code_hash=_hash(code)).first()
        if grant is None:
            return None
        grant.delete()  # single use, even when the redirect URI is wrong
        if grant.redirect_uri != redirect_uri or not grant.user.is_active:
            return None
        return grant
