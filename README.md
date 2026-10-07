# Belltower

A small, API-first shared to-do list for events. **Lists** hold **tasks**; tasks can have
subtasks and an optional expiration time. Old completed tasks, expired tasks and lists
that sit empty are cleaned up automatically.

## Running it

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # .venv/bin/pip on macOS/Linux
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver                         # web UI + API + MCP
python manage.py runscheduler                      # cleanup loop (separate process)
```

`runscheduler --once` runs one cleanup pass and exits, if you'd rather drive it from cron.

## Interfaces

| Path | What |
| --- | --- |
| `/` | Minimal web UI (lists, tasks, account/API token) |
| `/api/lists/`, `/api/tasks/` | REST API (browsable when logged in) |
| `/api/auth/token/` | `POST username` + `password` → API token |
| `/mcp` | MCP server for AI agents (Streamable HTTP) |
| `/admin/` | Django admin |

API clients, devices and agents authenticate with `Authorization: Bearer <token>`. Get a
token from the account page in the UI or from `/api/auth/token/`.

Task filters: `?list=<id>`, `?active=true` (not completed, not expired),
`?completed=true|false`, `?since=<ISO datetime>` (created after — useful for polling).

Users only see lists they're a member of. Creating a list makes you a member; pass
`users` (usernames) to add others.

`GET /api/users/?search=` lists active usernames (for sharing pickers). Lists created with
`persistent: true` are never cleaned up automatically (they stay when empty and keep their
completed and expired tasks); apps use this for long-lived lists such as event planning.

### Connecting an app (Town Hall)

Apps get an API key through a small OAuth-style handshake instead of copy-pasted tokens
(details in `accounts/connect.py`):

1. `GET /.well-known/belltower` returns the endpoints.
2. The app sends the browser to `/connect/authorize/?client_name=…&redirect_uri=…&state=…`;
   the user signs in and approves (always asked, even when already signed in).
3. Belltower redirects to `redirect_uri?code=…&state=…` (or `?error=access_denied`).
4. The app POSTs `code` + `redirect_uri` to `/connect/token/` and gets `{"api_key", "username"}`.
   Codes last five minutes and work once.

Each app gets its own key (only a hash is stored). Users disconnect apps under
**Connected apps** on the account page; an app can revoke its own key with
`POST /api/connect/revoke/`.

Session and CSRF cookies are named `belltower_sessionid` / `belltower_csrftoken`, so
running Belltower next to another Django app on the same host (e.g. Town Hall on
`:8000`, Belltower on `:8001`) doesn't sign you out of the other one.

### Connecting an AI agent

```sh
claude mcp add --transport http belltower http://localhost:8000/mcp \
  --header "Authorization: Bearer <token>"
```

Tools: `list_lists`, `create_list`, `rename_list`, `delete_list`, `list_tasks`,
`create_task`, `update_task`, `complete_task`, `delete_task`.

## Configuration (environment variables)

| Variable | Default | |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | dev key | Set in production |
| `DJANGO_DEBUG` | `1` | |
| `DJANGO_ALLOWED_HOSTS` | | Comma-separated |
| `DJANGO_TIME_ZONE` | `UTC` | Used for times entered/shown in the web UI |
| `BELLTOWER_COMPLETED_TASK_RETENTION_MINUTES` | `1440` | Delete completed tasks this long after completion |
| `BELLTOWER_EXPIRED_TASK_RETENTION_MINUTES` | `60` | Delete tasks this long after they expire |
| `BELLTOWER_EMPTY_LIST_RETENTION_MINUTES` | `1440` | Delete lists that have been empty this long |
| `BELLTOWER_CLEANUP_INTERVAL_SECONDS` | `300` | How often `runscheduler` runs |

Set any retention to `off` to disable that cleanup. A completed task that still has open
subtasks is kept.

## Single sign-on with Authentik (optional)

Username/password login always works. To add "Sign in with Authentik" next to it:

1. In Authentik, create an **OAuth2/OpenID Provider** (confidential client, signing key
   set so tokens are RS256) and an **Application** using it. Redirect URI:
   `https://<belltower-host>/oidc/callback/`.
2. Set:

   ```sh
   OIDC_ENABLED=1
   AUTHENTIK_URL=https://auth.example.com
   AUTHENTIK_APP_SLUG=belltower          # the application's slug
   OIDC_RP_CLIENT_ID=...
   OIDC_RP_CLIENT_SECRET=...
   ```

SSO users are linked by Authentik's `sub` claim (see `accounts.SsoIdentity`). On first SSO
login, an existing local account with the same username is linked; otherwise a new user
is created. Individual endpoints can be overridden with `OIDC_OP_*_ENDPOINT` variables.

## Roadmap: pagers

Pagers (e.g. ESP32 + LED display) will subscribe to one list and receive new tasks via
server-sent events. Until then they can poll
`GET /api/tasks/?list=<id>&active=true&since=<last seen created_at>` with a device user's
token. SSE will need the app served over ASGI (e.g. uvicorn).
