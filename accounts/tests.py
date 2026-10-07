from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import ApiKey, ConnectGrant, SsoIdentity

OIDC_SETTINGS = dict(
    OIDC_PROVIDER_NAME='Authentik',
    OIDC_RP_CLIENT_ID='client',
    OIDC_RP_CLIENT_SECRET='secret',
    OIDC_RP_SIGN_ALGO='RS256',
    OIDC_OP_AUTHORIZATION_ENDPOINT='https://auth.example.com/application/o/authorize/',
    OIDC_OP_TOKEN_ENDPOINT='https://auth.example.com/application/o/token/',
    OIDC_OP_USER_ENDPOINT='https://auth.example.com/application/o/userinfo/',
    OIDC_OP_JWKS_ENDPOINT='https://auth.example.com/application/o/belltower/jwks/',
)


@override_settings(**OIDC_SETTINGS)
class AuthentikBackendTests(TestCase):
    def setUp(self):
        from .oidc import AuthentikOIDCBackend
        self.backend = AuthentikOIDCBackend()
        self.claims = {'sub': 'abc123', 'preferred_username': 'alice', 'email': 'alice@example.com', 'given_name': 'Alice'}

    def test_creates_and_links_new_user(self):
        user = self.backend.create_user(self.claims)
        self.assertEqual((user.username, user.email, user.first_name), ('alice', 'alice@example.com', 'Alice'))
        self.assertTrue(SsoIdentity.objects.filter(user=user, provider='authentik', subject='abc123').exists())

    def test_links_existing_local_account_by_username(self):
        local = User.objects.create_user('alice', password='pw')
        self.assertEqual(list(self.backend.filter_users_by_claims(self.claims)), [local])
        self.backend.update_user(local, self.claims)
        # Once linked, the match follows the subject even if the username changes upstream.
        renamed = {**self.claims, 'preferred_username': 'alice.smith'}
        self.assertEqual(list(self.backend.filter_users_by_claims(renamed)), [local])
        self.assertTrue(local.check_password('pw'))  # password login keeps working

    def test_does_not_hijack_account_linked_to_another_subject(self):
        local = User.objects.create_user('alice')
        SsoIdentity.objects.create(user=local, provider='authentik', subject='someone-else')
        self.assertEqual(list(self.backend.filter_users_by_claims(self.claims)), [])
        self.assertIsNone(self.backend.create_user(self.claims))


class LoginPageTests(TestCase):
    def test_sso_button_hidden_by_default(self):
        self.assertNotContains(self.client.get('/accounts/login/'), 'Sign in with')


class ConnectFlowTests(TestCase):
    REDIRECT = 'https://hall.example.org/en/console/settings/backend/belltower/callback/'

    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')
        self.params = {'client_name': 'Town Hall', 'redirect_uri': self.REDIRECT, 'state': 'xyz'}

    def _approve(self):
        self.client.force_login(self.user)
        resp = self.client.post('/connect/authorize/', {**self.params, 'allow': '1'})
        query = parse_qs(urlsplit(resp['Location']).query)
        self.assertTrue(resp['Location'].startswith(self.REDIRECT))
        self.assertEqual(query['state'], ['xyz'])
        return query['code'][0]

    def test_well_known_lists_endpoints(self):
        data = self.client.get('/.well-known/belltower').json()
        self.assertEqual(data['service'], 'belltower')
        self.assertEqual(data['authorize_url'], 'http://testserver/connect/authorize/')
        self.assertEqual(data['token_url'], 'http://testserver/connect/token/')
        self.assertEqual(data['list_url'], 'http://testserver/lists/{id}/')

    def test_authorize_requires_login_and_asks_for_consent(self):
        resp = self.client.get('/connect/authorize/', self.params)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])
        self.client.force_login(self.user)
        resp = self.client.get('/connect/authorize/', self.params)
        self.assertContains(resp, 'Connect Town Hall?')
        self.assertContains(resp, 'hall.example.org')
        self.assertFalse(ConnectGrant.objects.exists())  # nothing issued until approved

    def test_rejects_bad_redirect_uri(self):
        self.client.force_login(self.user)
        for uri in ('javascript:alert(1)', '/relative/', 'https://x.org/#frag'):
            resp = self.client.get('/connect/authorize/', {**self.params, 'redirect_uri': uri})
            self.assertEqual(resp.status_code, 400)

    def test_deny_redirects_with_error(self):
        self.client.force_login(self.user)
        resp = self.client.post('/connect/authorize/', {**self.params, 'deny': '1'})
        self.assertEqual(resp['Location'], self.REDIRECT + '?error=access_denied&state=xyz')

    def test_full_flow_issues_working_key(self):
        code = self._approve()
        self.client.logout()
        resp = self.client.post('/connect/token/', {'code': code, 'redirect_uri': self.REDIRECT})
        data = resp.json()
        self.assertEqual(data['username'], 'alice')
        key = ApiKey.objects.get()
        self.assertEqual(key.name, 'Town Hall (hall.example.org)')
        self.assertNotEqual(key.key_hash, data['api_key'])  # only the hash is stored

        auth = {'HTTP_AUTHORIZATION': f"Bearer {data['api_key']}"}
        self.assertEqual(self.client.get('/api/lists/', **auth).status_code, 200)
        self.assertIsNotNone(ApiKey.objects.get().last_used_at)

        # The code works once.
        self.assertEqual(self.client.post('/connect/token/', {'code': code, 'redirect_uri': self.REDIRECT}).status_code, 400)

        # The app can revoke itself.
        self.assertEqual(self.client.post('/api/connect/revoke/', **auth).status_code, 204)
        self.assertEqual(self.client.get('/api/lists/', **auth).status_code, 401)

    def test_code_needs_matching_redirect_uri(self):
        code = self._approve()
        resp = self.client.post('/connect/token/', {'code': code, 'redirect_uri': 'https://evil.example/cb'})
        self.assertEqual(resp.status_code, 400)
        # ...and is burned by the failed attempt.
        self.assertEqual(self.client.post('/connect/token/', {'code': code, 'redirect_uri': self.REDIRECT}).status_code, 400)

    def test_code_expires(self):
        code = self._approve()
        ConnectGrant.objects.update(created_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(self.client.post('/connect/token/', {'code': code, 'redirect_uri': self.REDIRECT}).status_code, 400)

    def test_user_revokes_from_account_page(self):
        key, secret = ApiKey.issue(self.user, 'Town Hall (hall.example.org)')
        self.client.force_login(self.user)
        self.assertContains(self.client.get('/accounts/'), 'Town Hall (hall.example.org)')
        self.client.post(f'/accounts/keys/{key.pk}/revoke/')
        self.assertFalse(ApiKey.objects.exists())

    def test_personal_token_still_works(self):
        from rest_framework.authtoken.models import Token
        token = Token.objects.create(user=self.user)
        resp = self.client.get('/api/lists/', HTTP_AUTHORIZATION=f'Bearer {token.key}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.post('/api/connect/revoke/', HTTP_AUTHORIZATION=f'Bearer {token.key}').status_code, 400)
