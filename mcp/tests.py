import json

from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from tasks.models import List, Task


class McpTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice')
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.key}', HTTP_ACCEPT='application/json, text/event-stream')
        self.list = List.objects.create(name='Gala')
        self.list.add_member(self.user, admin=True)

    def rpc(self, method, params=None, id=1):
        message = {'jsonrpc': '2.0', 'method': method, 'id': id}
        if params is not None:
            message['params'] = params
        return self.client.post('/mcp', message, format='json')

    def call(self, tool_name, **arguments):
        resp = self.rpc('tools/call', {'name': tool_name, 'arguments': arguments})
        result = resp.json()['result']
        return result['isError'], json.loads(result['content'][0]['text']) if not result['isError'] else result['content'][0]['text']

    def test_requires_token(self):
        self.client.credentials()
        resp = self.rpc('ping')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp['WWW-Authenticate'], 'Bearer')

    def test_initialize_handshake(self):
        resp = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '1'}})
        result = resp.json()['result']
        self.assertEqual(result['protocolVersion'], '2025-06-18')
        self.assertIn('tools', result['capabilities'])
        notification = self.client.post('/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, format='json')
        self.assertEqual(notification.status_code, 202)

    def test_unknown_protocol_version_gets_latest(self):
        result = self.rpc('initialize', {'protocolVersion': '1999-01-01'}).json()['result']
        self.assertEqual(result['protocolVersion'], '2025-11-25')

    def test_tools_list(self):
        tools = {t['name']: t for t in self.rpc('tools/list').json()['result']['tools']}
        self.assertIn('create_task', tools)
        self.assertEqual(tools['create_task']['inputSchema']['required'], ['list_id', 'title'])

    def test_task_lifecycle(self):
        error, task = self.call('create_task', list_id=self.list.id, title='Chairs', expires_at='2030-01-01T10:00:00Z')
        self.assertFalse(error)
        self.assertEqual(task['expires_at'], '2030-01-01T10:00:00Z')

        _, tasks = self.call('list_tasks', list_id=self.list.id)
        self.assertEqual([t['title'] for t in tasks], ['Chairs'])

        _, task = self.call('complete_task', task_id=task['id'])
        self.assertTrue(task['completed'])
        _, tasks = self.call('list_tasks')
        self.assertEqual(tasks, [])

        _, task = self.call('update_task', task_id=task['id'], expires_at=None, completed=False)
        self.assertIsNone(task['expires_at'])
        self.call('delete_task', task_id=task['id'])
        self.assertFalse(Task.objects.exists())

    def test_list_tools(self):
        _, created = self.call('create_list', name='Load-in')
        self.assertEqual(created['users'], ['alice'])
        _, lists = self.call('list_lists')
        self.assertEqual(sorted(l['name'] for l in lists), ['Gala', 'Load-in'])
        self.call('rename_list', list_id=created['id'], name='Load out')
        self.call('delete_list', list_id=created['id'])
        self.assertEqual(list(List.objects.values_list('name', flat=True)), ['Gala'])

    def test_tool_errors_are_reported_to_the_agent(self):
        error, text = self.call('create_task', list_id=self.list.id)
        self.assertTrue(error)
        self.assertIn('title', text)

        other = List.objects.create(name='Private')
        error, text = self.call('create_task', list_id=other.id, title='x')
        self.assertTrue(error)

        error, text = self.call('delete_list', list_id=other.id)
        self.assertTrue(error)
        self.assertIn('Not found', text)

        error, text = self.call('update_task', task_id=1, list=other.id)
        self.assertTrue(error)

    def test_protocol_errors(self):
        self.assertEqual(self.rpc('nope').json()['error']['code'], -32601)
        self.assertEqual(self.rpc('tools/call', {'name': 'nope'}).json()['error']['code'], -32602)
        resp = self.client.post('/mcp', 'not json', content_type='application/json')
        self.assertEqual(resp.json()['error']['code'], -32700)
        self.assertEqual(self.client.get('/mcp').status_code, 405)


class McpRoleTests(APITestCase):
    def setUp(self):
        from tasks.models import List, Task
        self.admin = User.objects.create_user('alice')
        self.member = User.objects.create_user('bob')
        self.list = List.objects.create(name='Gala')
        self.list.add_member(self.admin, admin=True)
        self.list.add_member(self.member)
        self.task = Task.objects.create(title='Chairs', list=self.list)
        self.client.force_authenticate(self.member)

    def call(self, name, **arguments):
        resp = self.client.post('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                         'params': {'name': name, 'arguments': arguments}}, format='json')
        result = resp.json()['result']
        return result['isError'], result['content'][0]['text']

    def test_member_tools(self):
        error, text = self.call('delete_task', task_id=self.task.pk)
        self.assertTrue(error)
        self.assertIn('Not allowed', text)
        error, text = self.call('take_task', task_id=self.task.pk)
        self.assertFalse(error)
        self.assertIn('"assignee": "bob"', text)
        error, text = self.call('update_task', task_id=self.task.pk, title='Renamed')
        self.assertTrue(error)

    def test_merge_and_move_tools(self):
        from tasks.models import List
        other = List.objects.create(name='Other')
        other.add_member(self.member, admin=True)
        self.list.admins.add(self.member)
        error, text = self.call('move_task', task_id=self.task.pk, list_id=other.pk)
        self.assertFalse(error, text)
        error, text = self.call('merge_lists', target_list_id=self.list.pk, source_list_id=other.pk)
        self.assertFalse(error, text)
        self.assertIn('Gala & Other', text)
