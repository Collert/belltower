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
    task_list.add_member(user, admin=True)
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


class RolesTests(APITestCase):
    """Admins add, edit, assign and delete; members complete and take tasks themselves."""

    def setUp(self):
        self.admin = User.objects.create_user('alice', email='alice@example.org')
        self.member = User.objects.create_user('bob', email='Bob@Example.org')
        self.other = User.objects.create_user('carol', email='carol@example.org')
        self.list = make_list(self.admin)
        self.list.add_member(self.member)
        self.task = Task.objects.create(title='Set up chairs', list=self.list)

    def as_user(self, user):
        self.client.force_authenticate(user)

    def test_creator_is_admin(self):
        self.as_user(self.member)
        data = self.client.post('/api/lists/', {'name': 'Mine'}, format='json').json()
        self.assertTrue(data['is_admin'])
        self.assertEqual(data['admins'], ['bob'])

    def test_member_cannot_add_edit_or_delete(self):
        self.as_user(self.member)
        resp = self.client.post('/api/tasks/', {'title': 'X', 'list': self.list.pk})
        self.assertEqual(resp.status_code, 403)
        self.assertIn('Only admins', resp.json()['detail'])
        self.assertEqual(self.client.patch(f'/api/tasks/{self.task.pk}/', {'title': 'Y'}).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/tasks/{self.task.pk}/').status_code, 403)
        self.assertEqual(self.client.patch(f'/api/lists/{self.list.pk}/', {'name': 'Z'}).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/lists/{self.list.pk}/').status_code, 403)
        self.assertFalse(self.client.get(f'/api/lists/{self.list.pk}/').json()['is_admin'])

    def test_member_completes_and_takes_tasks(self):
        self.as_user(self.member)
        self.assertEqual(self.client.patch(f'/api/tasks/{self.task.pk}/', {'completed': True}).status_code, 200)
        resp = self.client.patch(f'/api/tasks/{self.task.pk}/', {'assignee': 'bob'})
        self.assertEqual(resp.json()['assignee'], 'bob')
        self.assertEqual(self.client.get('/api/tasks/?assignee=me').json()['count'], 1)
        # Can't hand it to someone else, but can let go.
        self.assertEqual(self.client.patch(f'/api/tasks/{self.task.pk}/', {'assignee': 'alice'}).status_code, 403)
        self.assertEqual(self.client.patch(f'/api/tasks/{self.task.pk}/', {'assignee': None}, format='json').status_code, 200)

    def test_member_cannot_take_someone_elses_task(self):
        self.task.assignee = self.admin
        self.task.save()
        self.as_user(self.member)
        resp = self.client.patch(f'/api/tasks/{self.task.pk}/', {'assignee': 'bob'})
        self.assertEqual(resp.status_code, 403)
        self.assertIn('assigned to alice', resp.json()['detail'])

    def test_admin_assigns_members_only(self):
        self.as_user(self.admin)
        resp = self.client.post('/api/tasks/', {'title': 'Food', 'list': self.list.pk, 'assignee': 'bob'})
        self.assertEqual(resp.json()['assignee'], 'bob')
        resp = self.client.patch(f'/api/tasks/{self.task.pk}/', {'assignee': 'carol'})
        self.assertEqual(resp.status_code, 400)  # carol isn't in the list

    def test_members_by_email(self):
        self.as_user(self.admin)
        url = f'/api/lists/{self.list.pk}/members/'
        data = self.client.post(url, {'email': 'CAROL@example.org', 'admin': True}).json()
        self.assertCountEqual(data['admins'], ['alice', 'carol'])
        carol = next(m for m in data['members'] if m['username'] == 'carol')
        self.assertEqual(carol, {'username': 'carol', 'name': '', 'email': 'carol@example.org', 'admin': True})
        # Posting again changes the role.
        data = self.client.post(url, {'email': 'carol@example.org', 'admin': False}).json()
        self.assertEqual(data['admins'], ['alice'])
        self.assertEqual(self.client.post(url, {'email': 'nobody@example.org'}).status_code, 404)

        self.task.assignee = self.other
        self.task.save()
        data = self.client.delete(f'{url}carol/').json()
        self.assertNotIn('carol', data['users'])
        self.task.refresh_from_db()
        self.assertIsNone(self.task.assignee)  # their tasks are unassigned

    def test_adding_without_admin_keeps_role(self):
        self.as_user(self.admin)
        url = f'/api/lists/{self.list.pk}/members/'
        self.client.post(url, {'email': 'carol@example.org', 'admin': True})
        data = self.client.post(url, {'username': 'carol'}).json()  # e.g. an app syncing sign-ups
        self.assertIn('carol', data['admins'])

    def test_member_cannot_manage_members(self):
        self.as_user(self.member)
        self.assertEqual(self.client.post(f'/api/lists/{self.list.pk}/members/', {'email': 'carol@example.org'}).status_code, 403)

    def test_web_ui_roles(self):
        self.client.force_login(self.member)
        page = self.client.get(f'/lists/{self.list.pk}/')
        self.assertNotContains(page, 'Add task')
        self.assertContains(page, 'Take it')
        self.assertEqual(self.client.post(f'/lists/{self.list.pk}/', {'title': 'Nope'}).status_code, 403)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/delete/').status_code, 403)
        self.client.post(f'/tasks/{self.task.pk}/claim/')
        self.task.refresh_from_db()
        self.assertEqual(self.task.assignee, self.member)
        self.assertContains(self.client.get(f'/lists/{self.list.pk}/'), "You're on it")

        self.client.force_login(self.admin)
        self.assertContains(self.client.get(f'/lists/{self.list.pk}/'), 'Add task')
        resp = self.client.post(f'/lists/{self.list.pk}/members/', {'who': 'nobody@example.org'})
        self.assertContains(resp, 'No Belltower account matches.')
        self.client.post(f'/lists/{self.list.pk}/members/', {'who': 'carol@example.org', 'admin': 'on'})
        self.assertTrue(self.list.is_admin(self.other))


class MergeAndMoveTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user('alice')
        self.bob = User.objects.create_user('bob')
        self.carol = User.objects.create_user('carol')
        self.client.force_authenticate(self.alice)
        self.kitchen = make_list(self.alice, 'Kitchen')
        self.kitchen.add_member(self.bob)
        self.setup = make_list(self.alice, 'Setup')
        self.setup.add_member(self.carol, admin=True)
        self.parent = Task.objects.create(title='Tables', list=self.setup, assignee=self.carol)
        self.child = Task.objects.create(title='Legs', list=self.setup, parent=self.parent)

    def test_merge(self):
        Task.objects.create(title='Cook', list=self.kitchen)
        data = self.client.post(f'/api/lists/{self.kitchen.pk}/merge/', {'list': self.setup.pk}).json()
        self.assertEqual(data['name'], 'Kitchen & Setup')
        self.assertCountEqual(data['users'], ['alice', 'bob', 'carol'])
        self.assertCountEqual(data['admins'], ['alice', 'carol'])
        self.assertEqual(self.kitchen.tasks.count(), 3)
        self.assertFalse(List.objects.filter(pk=self.setup.pk).exists())
        self.child.refresh_from_db()
        self.assertEqual(self.child.parent, self.parent)  # structure kept

    def test_merge_needs_admin_of_both(self):
        other = make_list(self.bob, 'Bob only')
        other.add_member(self.alice)  # alice is a plain member there
        self.assertEqual(self.client.post(f'/api/lists/{self.kitchen.pk}/merge/', {'list': other.pk}).status_code, 403)
        self.assertEqual(self.client.post(f'/api/lists/{self.kitchen.pk}/merge/', {'list': self.kitchen.pk}).status_code, 400)

    def test_move_task_brings_subtasks(self):
        resp = self.client.patch(f'/api/tasks/{self.parent.pk}/', {'list': self.kitchen.pk})
        self.assertEqual(resp.status_code, 200)
        self.child.refresh_from_db()
        self.parent.refresh_from_db()
        self.assertEqual(self.child.list, self.kitchen)
        self.assertIsNone(self.parent.assignee)  # carol isn't in Kitchen

    def test_move_subtask_alone_leaves_parent(self):
        self.client.patch(f'/api/tasks/{self.child.pk}/', {'list': self.kitchen.pk})
        self.child.refresh_from_db()
        self.assertEqual((self.child.list, self.child.parent), (self.kitchen, None))

    def test_member_cannot_move(self):
        self.client.force_authenticate(self.bob)
        task = Task.objects.create(title='Cook', list=self.kitchen)
        other = make_list(self.bob, 'Bob list')
        self.assertEqual(self.client.patch(f'/api/tasks/{task.pk}/', {'list': other.pk}).status_code, 403)

    def test_web_ui_rename_and_merge(self):
        self.client.force_login(self.alice)
        self.client.post(f'/lists/{self.kitchen.pk}/rename/', {'name': 'Food'})
        self.kitchen.refresh_from_db()
        self.assertEqual(self.kitchen.name, 'Food')
        self.assertContains(self.client.get(f'/lists/{self.kitchen.pk}/'), 'Merge another list')
        self.client.post(f'/lists/{self.kitchen.pk}/merge/', {'other': self.setup.pk})
        self.kitchen.refresh_from_db()
        self.assertEqual(self.kitchen.name, 'Food & Setup')
