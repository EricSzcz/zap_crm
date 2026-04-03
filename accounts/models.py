from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", "admin")
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    objects = UserManager()
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        AGENT = "agent", "Agent"

    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.AGENT,
    )

    def is_admin_role(self) -> bool:
        return self.role == self.Role.ADMIN

    def is_agent_role(self) -> bool:
        return self.role == self.Role.AGENT

    def __str__(self) -> str:
        return f"{self.username} ({self.get_role_display()})"
