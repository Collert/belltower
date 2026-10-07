"""Connecting other apps (e.g. Town Hall) to Belltower.

A small OAuth-style handshake that ends with the app holding an ApiKey for the user
who approved it:

1. The app reads GET /.well-known/belltower to find the endpoints below.
2. It sends the user's browser to /connect/authorize/?client_name=…&redirect_uri=…&state=…
   The user signs in if needed and approves (or cancels) the connection.
3. Belltower redirects to redirect_uri?code=…&state=… (or ?error=access_denied&state=…).
   The app checks that `state` matches what it sent.
4. The app POSTs code + redirect_uri to /connect/token/ (server to server) and gets back
   {"api_key": …, "username": …}. Codes last five minutes and work once.
5. The app sends "Authorization: Bearer <api_key>" to the REST API (and MCP). It can
   revoke its own key with POST /api/connect/revoke/; users revoke keys on their
   account page.
"""
from urllib.parse import urlencode, urlsplit

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import ApiKey, ConnectGrant

PROTOCOL_VERSION = 1


@require_GET
def well_known(request):
    absolute = request.build_absolute_uri
    response = JsonResponse({
        'service': 'belltower',
        'version': PROTOCOL_VERSION,
        'authorize_url': absolute(reverse('connect-authorize')),
        'token_url': absolute(reverse('connect-token')),
        'revoke_url': absolute(reverse('connect-revoke')),
        'api_url': absolute('/api/'),
        'mcp_url': absolute(reverse('mcp')),
        # Web UI page for a list; replace {id}.
        'list_url': absolute('/lists/') + '{id}/',
    })
    response['Access-Control-Allow-Origin'] = '*'
    return response


def _valid_redirect_uri(uri):
    parts = urlsplit(uri)
    return parts.scheme in ('http', 'https') and bool(parts.netloc) and not parts.fragment


def _with_params(uri, **params):
    return uri + ('&' if '?' in uri else '?') + urlencode(params)


@login_required
def authorize(request):
    source = request.POST if request.method == 'POST' else request.GET
    client_name = source.get('client_name', '').strip()[:100]
    redirect_uri = source.get('redirect_uri', '').strip()
    state = source.get('state', '')
    # Never redirect somewhere we couldn't validate: show an error page instead.
    if not client_name or not state or not _valid_redirect_uri(redirect_uri):
        return HttpResponseBadRequest('Invalid connection request: client_name, state and an http(s) redirect_uri are required.')

    if request.method == 'POST':
        if 'allow' not in request.POST:
            return redirect(_with_params(redirect_uri, error='access_denied', state=state))
        code = ConnectGrant.issue(request.user, client_name, redirect_uri)
        return redirect(_with_params(redirect_uri, code=code, state=state))

    # Always ask, even when already signed in: approving hands the app a key that acts
    # as this user, so a site mustn't get one just by redirecting a signed-in browser here.
    return render(request, 'accounts/connect_authorize.html', {
        'client_name': client_name,
        'client_host': urlsplit(redirect_uri).netloc,
        'redirect_uri': redirect_uri,
        'state': state,
    })


@csrf_exempt  # server to server; the one-time code is the credential
@require_POST
def token(request):
    code = request.POST.get('code', '')
    redirect_uri = request.POST.get('redirect_uri', '')
    grant = ConnectGrant.redeem(code, redirect_uri) if code and redirect_uri else None
    if grant is None:
        return JsonResponse({'error': 'invalid_grant', 'detail': 'The code is invalid, expired or already used.'}, status=400)
    name = f'{grant.client_name} ({urlsplit(grant.redirect_uri).netloc})'
    api_key, secret = ApiKey.issue(grant.user, name)
    return JsonResponse({'api_key': secret, 'key_id': api_key.pk, 'username': grant.user.get_username()})


@api_view(['POST'])
def revoke(request):
    """Revoke the API key this request was made with (an app disconnecting itself)."""
    if not isinstance(request.auth, ApiKey):
        return Response({'detail': 'Only keys issued through /connect/ can be revoked here.'}, status=400)
    request.auth.delete()
    return Response(status=204)
