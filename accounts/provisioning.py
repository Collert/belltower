"""Accounts for people who come from a connected app (e.g. Town Hall).

An app connected with a *staff* user's key can:

- ``GET /api/me/``: who the key acts as, and whether it may manage accounts.
- ``POST /api/users/link/`` with ``email`` (+ ``first_name``, ``last_name``): find the
  active account with that email, or create one (no password). Returns its username and
  ``created``. Matching is case-insensitive; two accounts with one email give 409.
- ``POST /api/users/<username>/login-link/`` with ``next`` (a path): a one-time URL that
  signs that person in and opens ``next``. Links last two minutes and are never issued
  for staff or superusers, so an app key can't be used to sign in as an admin.
"""
import re

from django.contrib.auth import get_user_model, login
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .models import LoginLink


def _require_staff(request):
    if not request.user.is_staff:
        raise PermissionDenied('Only staff accounts can manage Belltower accounts.')


def _available_username(email):
    users = get_user_model().objects
    base = re.sub(r'[^\w.@+-]', '', email.split('@')[0])[:140] or 'user'
    username, n = base, 1
    while users.filter(username=username).exists():
        n += 1
        username = f'{base}{n}'
    return username


def _user_data(user, **extra):
    return {'username': user.get_username(), 'email': user.email, 'name': user.get_full_name(), **extra}


@api_view(['GET'])
def me(request):
    return Response(_user_data(request.user, is_staff=request.user.is_staff))


@api_view(['POST'])
def link_user(request):
    _require_staff(request)
    email = (request.data.get('email') or '').strip()
    if '@' not in email:
        raise ValidationError({'email': 'Give an email address.'})
    users = get_user_model().objects
    matches = list(users.filter(email__iexact=email, is_active=True)[:2])
    if len(matches) > 1:
        return Response({'detail': 'More than one Belltower account uses that email.'}, status=409)
    if matches:
        return Response(_user_data(matches[0], created=False))
    user = users.create_user(
        _available_username(email), email=email,
        first_name=(request.data.get('first_name') or '')[:150], last_name=(request.data.get('last_name') or '')[:150],
    )  # no password: they sign in through login links (or SSO)
    return Response(_user_data(user, created=True), status=201)


@api_view(['POST'])
def login_link(request, username):
    _require_staff(request)
    user = get_object_or_404(get_user_model(), username=username, is_active=True)
    if user.is_staff or user.is_superuser:
        raise PermissionDenied('Sign-in links are not issued for staff accounts.')
    next_url = request.data.get('next') or '/'
    if not next_url.startswith('/') or next_url.startswith('//'):
        raise ValidationError({'next': 'Give a path on this server, e.g. /lists/3/.'})
    token = LoginLink.issue(user, next_url)
    return Response({'url': request.build_absolute_uri(reverse('login-link', args=[token]))})


@require_GET
def use_login_link(request, token):
    link = LoginLink.redeem(token)
    if link is None:
        return HttpResponseBadRequest('This sign-in link has expired or was already used. Open it again from the app.')
    login(request, link.user, backend='django.contrib.auth.backends.ModelBackend')
    safe = url_has_allowed_host_and_scheme(link.next_url, allowed_hosts={request.get_host()})
    return redirect(link.next_url if safe else '/')
