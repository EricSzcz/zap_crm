"""Minimal settings overlay for test runs — uses SQLite, no Redis required."""
from .settings import *  # noqa: F401, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Use in-memory channel layer (no Redis needed for unit tests)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Disable Celery task execution during tests
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",  # fast hashing for tests
]

# Dummy fernet key (32 url-safe base64 bytes)
FERNET_KEYS = ["fFdmGEBQBqGnwYPBmNJjqnMxHuAJ5VuFkBjVvfX8OWE="]
META_WEBHOOK_APP_SECRET = "test-webhook-secret"
