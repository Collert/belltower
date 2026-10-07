from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .cleanup import run_cleanup
from .models import List, Task


def make_list(user, name='Event'):
    task_list = List.objects.create(name=name)
    task_list.users.add(user)
    return task_list


class TaskApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')
        self.other = User.objects.create_user('bob', password='pw')
        self.client.force_authenticate(self.user)

    def test_requires_auth(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/tasks/').status_code, 401)

    def test_bearer_token_auth(self):
        self.client.force_authenticate(None)
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.key}')
        self.assertEqual(self.client.get('/api/tasks/').status_code, 200)

    def test_obtain_token_with_password(self):
        self.client.force_authenticate(None)
        resp = self.client.post('/api/auth/token/', {'username': 'alice', 'password': 'pw'})
        self.assertEqual(resp.data['token'], Token.objects.get(user=self.user).key)

    def test_create_list_adds_creator_and_members(self):
        resp = self.client.post('/api/lists/', {'name': 'Groceries', 'users': ['bob']}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertCountEqual(resp.data['users'], ['alice', 'bob'])

    def test_create_task_and_subtask(self):
        lst = make_list(self.user)
        parent = self.client.post('/api/tasks/', {'title': 'A', 'list': lst.id}).data
        self.assertIsNotNone(parent['created_at'])
        resp = self.client.post('/api/tasks/', {'title': 'B', 'list': lst.id, 'parent': parent['id']})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.client.get(f"/api/tasks/{parent['id']}/").data['subtasks'], [resp.data['id']])

    def test_task_requires_list(self):
        resp = self.client.post('/api/tasks/', {'title': 'Orphan'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('list', resp.data)

    def test_subtask_must_share_parent_list(self):
        parent = Task.objects.create(title='A', list=make_list(self.user, 'one'))
        resp = self.client.post('/api/tasks/', {'title': 'B', 'list': make_list(self.user, 'two').id, 'parent': parent.id})
        self.assertEqual(resp.status_code, 400)

    def test_completing_sets_completed_at(self):
        task = Task.objects.create(title='A', list=make_list(self.user))
        data = self.client.patch(f'/api/tasks/{task.id}/', {'completed': True}).data
        self.assertIsNotNone(data['completed_at'])
        data = self.client.patch(f'/api/tasks/{task.id}/', {'completed': False}).data
        self.assertIsNone(data['completed_at'])

    def test_filters(self):
        lst = make_list(self.user)
        old = Task.objects.create(title='old', list=lst)
        Task.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=1))
        Task.objects.create(title='done', list=lst, completed=True)
        Task.objects.create(title='expired', list=lst, expires_at=timezone.now() - timedelta(minutes=1))
        Task.objects.create(title='new', list=lst, expires_at=timezone.now() + timedelta(hours=1))
        Task.objects.create(title='elsewhere', list=make_list(self.user, 'other'))

        def titles(query):
            return [t['title'] for t in self.client.get(f'/api/tasks/?list={lst.id}&{query}').data['results']]

        self.assertEqual(titles('active=true'), ['old', 'new'])
        self.assertEqual(titles('completed=true'), ['done'])
        since = (timezone.now() - timedelta(minutes=30)).isoformat().replace('+00:00', 'Z')
        self.assertNotIn('old', titles(f'since={since}'))
        self.assertEqual(self.client.get('/api/tasks/?since=nope').status_code, 400)
        self.assertEqual(self.client.get('/api/tasks/?list=nope').status_code, 400)

    def test_users_endpoint(self):
        User.objects.create_user('carol', first_name='Carol', last_name='King')
        data = self.client.get('/api/users/?search=car').json()
        self.assertEqual(data['results'], [{'username': 'carol', 'name': 'Carol King'}])

    def test_persistent_flag_and_sharing(self):
        resp = self.client.post('/api/lists/', {'name': 'Gala planning', 'persistent': True, 'users': ['bob']}, format='json')
        self.assertTrue(resp.json()['persistent'])
        self.assertCountEqual(resp.json()['users'], ['alice', 'bob'])

    def test_cannot_see_or_use_other_users_lists(self):
        lst = make_list(self.other, 'Private')
        task = Task.objects.create(title='secret', list=lst)
        self.assertEqual(self.client.get('/api/tasks/').data['count'], 0)
        self.assertEqual(self.client.get(f'/api/lists/{lst.id}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/tasks/{task.id}/').status_code, 404)
        resp = self.client.post('/api/tasks/', {'title': 'x', 'list': lst.id})
        self.assertEqual(resp.status_code, 400)


@override_settings(BELLTOWER={
    'COMPLETED_TASK_RETENTION': timedelta(hours=1),
    'EXPIRED_TASK_RETENTION': timedelta(minutes=10),
    'EMPTY_LIST_RETENTION': timedelta(hours=2),
    'CLEANUP_INTERVAL_SECONDS': 60,
})
class CleanupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice')
        self.list = make_list(self.user)
        self.now = timezone.now()

    def test_deletes_old_completed_tasks_only(self):
        recent = Task.objects.create(title='recent', list=self.list, completed=True)
        old = Task.objects.create(title='old', list=self.list, completed=True)
        Task.objects.filter(pk=old.pk).update(completed_at=self.now - timedelta(hours=2))
        Task.objects.create(title='open', list=self.list)

        self.assertEqual(run_cleanup(self.now)['completed_tasks'], 1)
        self.assertQuerySetEqual(Task.objects.order_by('title'), ['open', 'recent'], transform=str)
        self.assertTrue(Task.objects.filter(pk=recent.pk).exists())

    def test_keeps_completed_parent_with_open_subtasks(self):
        parent = Task.objects.create(title='parent', list=self.list, completed=True)
        Task.objects.filter(pk=parent.pk).update(completed_at=self.now - timedelta(hours=2))
        Task.objects.create(title='child', list=self.list, parent=parent)
        self.assertEqual(run_cleanup(self.now)['completed_tasks'], 0)

    def test_deletes_expired_tasks_after_grace_period(self):
        Task.objects.create(title='just expired', list=self.list, expires_at=self.now - timedelta(minutes=5))
        Task.objects.create(title='long expired', list=self.list, expires_at=self.now - timedelta(minutes=30))
        self.assertEqual(run_cleanup(self.now)['expired_tasks'], 1)
        self.assertEqual(list(Task.objects.values_list('title', flat=True)), ['just expired'])

    def test_deletes_lists_empty_for_too_long(self):
        busy = make_list(self.user, 'busy')
        Task.objects.create(title='t', list=busy)
        fresh = make_list(self.user, 'fresh')
        List.objects.filter(pk__in=[self.list.pk, busy.pk]).update(last_activity_at=self.now - timedelta(hours=3))

        self.assertEqual(run_cleanup(self.now)['empty_lists'], 1)
        self.assertCountEqual(List.objects.values_list('name', flat=True), ['busy', 'fresh'])
        self.assertTrue(List.objects.filter(pk=fresh.pk).exists())

    def test_removing_last_task_restarts_empty_clock(self):
        List.objects.filter(pk=self.list.pk).update(last_activity_at=self.now - timedelta(hours=3))
        Task.objects.create(title='t', list=self.list).delete()
        self.assertEqual(run_cleanup(self.now)['empty_lists'], 0)

    def test_persistent_lists_are_left_alone(self):
        self.list.persistent = True
        self.list.save()
        done = Task.objects.create(title='done', list=self.list, completed=True)
        Task.objects.filter(pk=done.pk).update(completed_at=self.now - timedelta(days=3))
        Task.objects.create(title='expired', list=self.list, expires_at=self.now - timedelta(days=3))
        empty = make_list(self.user, 'empty planning')
        List.objects.filter(pk=empty.pk).update(persistent=True, last_activity_at=self.now - timedelta(days=3))

        self.assertEqual(run_cleanup(self.now), {'completed_tasks': 0, 'expired_tasks': 0, 'empty_lists': 0})

    @override_settings(BELLTOWER={
        'COMPLETED_TASK_RETENTION': None, 'EXPIRED_TASK_RETENTION': None,
        'EMPTY_LIST_RETENTION': None, 'CLEANUP_INTERVAL_SECONDS': 60,
    })
    def test_disabled_retention(self):
        List.objects.filter(pk=self.list.pk).update(last_activity_at=self.now - timedelta(days=30))
        self.assertEqual(run_cleanup(self.now), {'completed_tasks': 0, 'expired_tasks': 0, 'empty_lists': 0})


class WebUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')
        self.client.force_login(self.user)

    def test_login_required(self):
        self.client.logout()
        self.assertRedirects(self.client.get('/'), '/accounts/login/?next=/')
        self.assertContains(self.client.get('/accounts/login/'), 'Sign in')

    def test_list_and_task_flow(self):
        resp = self.client.post('/', {'name': 'Gala'})
        lst = List.objects.get(name='Gala')
        self.assertRedirects(resp, f'/lists/{lst.pk}/')

        self.client.post(f'/lists/{lst.pk}/', {'title': 'Set up chairs', 'expires_at': '2030-01-01T10:00'})
        task = lst.tasks.get()
        self.assertEqual(task.expires_at.year, 2030)
        self.assertContains(self.client.get(f'/lists/{lst.pk}/'), 'Set up chairs')
        self.assertContains(self.client.get('/'), '1 open')

        self.client.post(f'/tasks/{task.pk}/toggle/')
        task.refresh_from_db()
        self.assertTrue(task.completed)
        self.assertIsNotNone(task.completed_at)

        self.client.post(f'/tasks/{task.pk}/delete/')
        self.assertFalse(lst.tasks.exists())
        self.client.post(f'/lists/{lst.pk}/delete/')
        self.assertFalse(List.objects.exists())

    def test_other_users_list_is_404(self):
        lst = make_list(User.objects.create_user('bob'))
        self.assertEqual(self.client.get(f'/lists/{lst.pk}/').status_code, 404)

    def test_account_token(self):
        self.client.post('/accounts/token/')
        token = Token.objects.get(user=self.user)
        self.assertContains(self.client.get('/accounts/'), token.key)
