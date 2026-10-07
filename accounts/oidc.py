import logging

from django.conf import settings
from django.db import transaction
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .models import SsoIdentity

logger = logging.getLogger(__name__)


class AuthentikOIDCBackend(OIDCAuthenticationBackend):
    """Signs users in through Authentik, alongside regular username/password login.

    Matching order: an existing SSO link for the `sub` claim, then a local user with
    the same username that isn't linked yet (so existing accounts carry over), and
    otherwise a new user is created.
    """

    @property
    def provider(self):
        return settings.OIDC_PROVIDER_NAME.lower()

    def filter_users_by_claims(self, claims):
        subject = claims.get('sub')
        if not subject:
            return self.UserModel.objects.none()
        linked = self.UserModel.objects.filter(
            sso_identities__provider=self.provider, sso_identities__subject=subject
        )
        if linked.exists():
            return linked
        username = claims.get('preferred_username')
        if not username:
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(username=username).exclude(sso_identities__provider=self.provider)

    @transaction.atomic
    def create_user(self, claims):
        username = claims.get('preferred_username')
        if not username or self.UserModel.objects.filter(username=username).exists():
            # The username belongs to someone already linked to a different SSO account.
            logger.warning('Refusing SSO login for sub=%s: username %r is unavailable.', claims.get('sub'), username)
            return None
        user = self.UserModel.objects.create_user(username=username)
        return self.update_user(user, claims)

    def update_user(self, user, claims):
        SsoIdentity.objects.get_or_create(user=user, provider=self.provider, subject=claims['sub'])
        user.email = claims.get('email', user.email)
        user.first_name = claims.get('given_name', user.first_name)
        user.last_name = claims.get('family_name', user.last_name)
        user.save()
        return user
