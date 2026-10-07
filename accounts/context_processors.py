from django.conf import settings


def sso(request):
    return {
        'sso_enabled': settings.OIDC_ENABLED,
        'sso_provider_name': settings.OIDC_PROVIDER_NAME,
    }
