"""A Model Context Protocol server over the Streamable HTTP transport.

Agents POST JSON-RPC messages to /mcp and get a single JSON response back; the server
doesn't push messages, so GET (the optional SSE stream) answers 405. Authentication is the
same bearer token as the REST API.

This is implemented directly instead of with the `mcp` Python SDK because the SDK's
package name collides with this app's name.
"""

import json

from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from rest_framework import exceptions
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.utils.encoders import JSONEncoder
from rest_framework.views import APIView

from accounts.authentication import BearerTokenAuthentication

from .tools import TOOLS

SUPPORTED_PROTOCOL_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26')
SERVER_INFO = {'name': 'belltower', 'title': 'Belltower', 'version': '0.1.0'}
INSTRUCTIONS = (
    'Belltower is a simple shared to-do system for events: lists contain tasks, tasks can have '
    'subtasks, an optional expiration time and an assignee. Use list_lists to find list ids before working '
    'with tasks. In lists where you are an admin (is_admin) you can add, edit, assign and delete tasks; in '
    'other lists you can complete tasks and take them on yourself with take_task.'
)

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


class JsonRpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _error(message_id, code, message):
    return {'jsonrpc': '2.0', 'id': message_id, 'error': {'code': code, 'message': message}}


def _tool_result(payload, is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload, cls=JSONEncoder, indent=2)
    return {'content': [{'type': 'text', 'text': text}], 'isError': is_error}


def call_tool(request, params):
    name = params.get('name')
    if name not in TOOLS:
        raise JsonRpcError(INVALID_PARAMS, f'Unknown tool: {name}')
    arguments = params.get('arguments') or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(INVALID_PARAMS, 'Tool arguments must be an object.')
    # Problems the agent can fix by changing its call come back as tool errors it can read.
    try:
        return _tool_result(TOOLS[name].handler(request, **arguments))
    except TypeError as e:
        return _tool_result(f'Invalid arguments: {e}', is_error=True)
    except exceptions.ValidationError as e:
        return _tool_result({'validation_errors': e.detail}, is_error=True)
    except exceptions.PermissionDenied as e:
        return _tool_result(f'Not allowed: {e.detail}', is_error=True)
    except (Http404, ObjectDoesNotExist):
        return _tool_result('Not found (or you are not a member of that list).', is_error=True)


def handle(request, method, params):
    if method == 'initialize':
        requested = params.get('protocolVersion')
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        return {
            'protocolVersion': version,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': SERVER_INFO,
            'instructions': INSTRUCTIONS,
        }
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [t.definition() for t in TOOLS.values()]}
    if method == 'tools/call':
        return call_tool(request, params)
    raise JsonRpcError(METHOD_NOT_FOUND, f'Method not found: {method}')


class McpView(APIView):
    authentication_classes = [BearerTokenAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def post(self, request):
        try:
            message = request.data
        except exceptions.ParseError:
            return Response(_error(None, PARSE_ERROR, 'Parse error'), status=400)

        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            return Response(_error(None, INVALID_REQUEST, 'Expected a single JSON-RPC 2.0 request.'), status=400)

        if 'id' not in message:
            # Notifications (e.g. notifications/initialized) need no reply.
            return Response(status=202)

        params = message.get('params') or {}
        try:
            result = handle(request, message['method'], params)
        except JsonRpcError as e:
            return Response(_error(message['id'], e.code, e.message))
        return Response({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
