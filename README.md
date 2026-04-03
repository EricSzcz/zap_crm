# Zap CRM

Personal project developed independently by Eric Szczepanik to demonstrate API architecture patterns applied in production environments.

A multi-user WhatsApp CRM built with Django 4.2, Django Channels, Celery, and HTMX. Supports role-based access (admin/agent), encrypted WhatsApp token storage, real-time WebSocket chat, and async task processing.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Django 4.2 |
| ASGI server | Daphne 4.1 |
| WebSockets | Django Channels 4.1 + channels-redis |
| Task queue | Celery 5.4 |
| Database | PostgreSQL 16 |
| Cache / broker | Redis 7 |
| Encryption | cryptography (Fernet) |
| Package manager | [uv](https://github.com/astral-sh/uv) |

---

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (`pip install uv` or `brew install uv`)
- Docker & Docker Compose (for dependency services or full-stack run)

---

## Quick Start (local dev with Docker services)

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd zap_crm
uv sync
```

### 2. Set up environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in the required values:

```bash
# Generate a Django secret key
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# Generate a Fernet encryption key (for WhatsApp token storage)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `SECRET_KEY`, `FERNET_KEY`, and optionally `META_WEBHOOK_APP_SECRET` in your `.env`.

### 3. Start dependency services (PostgreSQL + Redis)

```bash
docker compose up db redis -d
```

This starts:
- PostgreSQL 16 on `localhost:5433`
- Redis 7 on `localhost:6380`

### 4. Run database migrations

```bash
uv run python manage.py migrate
```

### 5. Create a superuser

```bash
uv run python manage.py createsuperuser
```

### 6. Start the ASGI server (Daphne)

```bash
uv run daphne -b 0.0.0.0 -p 8000 zap_crm.asgi:application
```

### 7. Start the Celery worker (separate terminal)

```bash
uv run celery -A zap_crm worker -l info
```

The app is now available at `http://localhost:8000`.

---

## Running with Docker Compose (full stack)

Builds and runs all four services (web, worker, db, redis) together:

```bash
docker compose up --build
```

To run in detached mode:

```bash
docker compose up --build -d
```

To view logs:

```bash
docker compose logs -f web
docker compose logs -f worker
```

To stop all services:

```bash
docker compose down
```

To stop and remove volumes (wipes the database):

```bash
docker compose down -v
```

---

## Running Tests

Tests use SQLite in-memory (no PostgreSQL needed) and `InMemoryChannelLayer` (no Redis needed).

```bash
uv run pytest tests/ -v
```

Run a specific test file:

```bash
uv run pytest tests/test_accounts.py -v
```

Run a specific test:

```bash
uv run pytest tests/test_accounts.py::TestLoginView::test_valid_login -v
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | Django secret key | *(required)* |
| `DEBUG` | Enable debug mode | `True` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `DB_NAME` | PostgreSQL database name | `zap_crm` |
| `DB_USER` | PostgreSQL username | `zap_user` |
| `DB_PASSWORD` | PostgreSQL password | `zap_pass` |
| `DB_HOST` | PostgreSQL host | `localhost` |
| `DB_PORT` | PostgreSQL port (host-mapped) | `5433` |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6380/0` |
| `FERNET_KEY` | Fernet key for token encryption | *(required)* |
| `META_WEBHOOK_APP_SECRET` | Meta webhook app secret | *(optional)* |

> Inside Docker Compose, `DB_HOST`, `DB_PORT`, and `REDIS_URL` are overridden automatically to use container-internal addresses.

---

## Project Structure

```
zap_crm/
├── accounts/          # Custom User model, login/logout, role mixins
├── chat/              # WebSocket consumers, real-time chat (Django Channels)
├── conversations/     # Contact, Conversation, Message models + querysets
├── core/              # Shared utilities (EncryptedTextField)
├── webhooks/          # Meta/WhatsApp webhook handlers
├── whatsapp_config/   # WhatsApp account management, admin panel
├── tests/             # All tests (pytest-django)
├── zap_crm/           # Django project settings, ASGI, WSGI, Celery app
├── templates/         # Django HTML templates
├── static/            # Static assets
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

---

## URL Structure

| Path | App | Description |
|---|---|---|
| `/login/` | accounts | Login page |
| `/logout/` | accounts | Logout |
| `/admin-panel/` | whatsapp_config | WhatsApp account management |
| `/chat/` | chat | Real-time chat interface |
| `/webhooks/` | webhooks | Meta webhook endpoint |

---

## Dependency Services

| Service | Image | Host Port | Purpose |
|---|---|---|---|
| PostgreSQL | `postgres:16-alpine` | `5433` | Primary database |
| Redis | `redis:7-alpine` | `6380` | Channel layer + Celery broker |

> Host ports are offset from defaults (5433 vs 5432, 6380 vs 6379) to avoid conflicts with locally installed instances.

---

## Exposing Webhooks Locally with ngrok

The Meta WhatsApp Business API requires a publicly accessible HTTPS URL to deliver webhook events. Use ngrok to tunnel your local server during development.

### 1. Install ngrok

```bash
# macOS
brew install ngrok

# Or download from https://ngrok.com/download
```

### 2. Authenticate ngrok (one-time setup)

Sign up at [ngrok.com](https://ngrok.com), then:

```bash
ngrok config add-authtoken <your-authtoken>
```

### 3. Start your local server

Make sure Daphne is running on port 8000:

```bash
uv run daphne -b 0.0.0.0 -p 8000 zap_crm.asgi:application
```

### 4. Start the ngrok tunnel

```bash
ngrok http 8000
```

ngrok will output a public URL like:

```
Forwarding  https://abc123.ngrok-free.app -> http://localhost:8000
```

### 5. Update Django settings

Add the ngrok hostname to `ALLOWED_HOSTS` in your `.env`:

```
ALLOWED_HOSTS=localhost,127.0.0.1,abc123.ngrok-free.app
```

> If you want to avoid editing `.env` on every ngrok restart, use a static domain (available on paid plans) or set `ALLOWED_HOSTS=*` temporarily in development only.

### 6. Configure the Meta webhook

In the [Meta for Developers dashboard](https://developers.facebook.com/), set your webhook URL to:

```
https://abc123.ngrok-free.app/webhooks/
```

Set the **Verify Token** to match `META_WEBHOOK_APP_SECRET` in your `.env`.

### Persistent ngrok domain (optional)

If you have a paid ngrok plan, you can reserve a static domain and avoid updating the Meta dashboard on every restart:

```bash
ngrok http --domain=your-reserved-domain.ngrok-free.app 8000
```

### ngrok web inspector

While ngrok is running, you can inspect and replay webhook requests at:

```
http://localhost:4040
```
