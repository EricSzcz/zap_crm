"""
Custom encrypted model field using the cryptography library's Fernet symmetric
encryption.  Replaces django-fernet-fields which is not compatible with
Django 4.x (it used the removed `force_text` helper).

Usage
-----
    from core.fields import EncryptedTextField

    class MyModel(models.Model):
        secret = EncryptedTextField()

Settings
--------
    FERNET_KEYS = ["<base64-url-safe-32-byte-key>"]

    Generate a new key:
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
"""
from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.db import models


def _get_fernet() -> MultiFernet:
    """Build a MultiFernet from FERNET_KEYS to support key rotation."""
    keys = getattr(settings, "FERNET_KEYS", [])
    if not keys or not keys[0]:
        raise ValueError(
            "settings.FERNET_KEYS must contain at least one valid Fernet key. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])


class EncryptedTextField(models.TextField):
    """
    TextField that transparently encrypts values before storing them in the
    database and decrypts them when read back.

    The ciphertext is stored as a URL-safe base64 string (the Fernet token).
    Values are always str → str.
    """

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return _get_fernet().decrypt(value.encode()).decode()

    def to_python(self, value):
        # Called when deserialising from a form or fixture — value is already plain text
        return value

    def get_prep_value(self, value):
        if value is None:
            return value
        return _get_fernet().encrypt(value.encode()).decode()
