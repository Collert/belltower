"""
URL configuration for belltower project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from rest_framework.authtoken.views import obtain_auth_token

from accounts import connect, provisioning
from mcp.views import McpView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('api/auth/token/', obtain_auth_token, name='api-token'),
    # Connecting apps such as Town Hall (see accounts/connect.py).
    path('.well-known/belltower', connect.well_known, name='well-known'),
    path('connect/authorize/', connect.authorize, name='connect-authorize'),
    path('connect/token/', connect.token, name='connect-token'),
    path('api/connect/revoke/', connect.revoke, name='connect-revoke'),
    # Accounts for a connected app's people (see accounts/provisioning.py). Before the
    # router so users/link/ isn't read as a username.
    path('api/me/', provisioning.me, name='api-me'),
    path('api/users/link/', provisioning.link_user, name='api-link-user'),
    path('api/users/<str:username>/login-link/', provisioning.login_link, name='api-login-link'),
    path('connect/login/<str:token>/', provisioning.use_login_link, name='login-link'),
    path('api/', include('tasks.api_urls')),
    path('api-auth/', include('rest_framework.urls')),
    # No trailing slash: MCP clients POST to the exact URL they're given.
    path('mcp', McpView.as_view(), name='mcp'),
    path('', include('tasks.urls')),
]

if settings.OIDC_ENABLED:
    urlpatterns.append(path('oidc/', include('mozilla_django_oidc.urls')))
