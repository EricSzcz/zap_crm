from django.conf import settings
from django.db import models

from core.fields import EncryptedTextField


class WhatsAppAccount(models.Model):
    name = models.CharField(max_length=100)
    phone_number_id = models.CharField(max_length=100, unique=True)
    api_token = EncryptedTextField()
    webhook_verify_token = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "WhatsApp Account"
        verbose_name_plural = "WhatsApp Accounts"

    def __str__(self) -> str:
        status = "active" if self.is_active else "inactive"
        return f"{self.name} ({self.phone_number_id}) [{status}]"

    def get_api_headers(self) -> dict:
        """Return the Authorization header dict for Meta API calls."""
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }


class AccountAssignment(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_assignments",
        limit_choices_to={"role": "agent"},
    )
    account = models.ForeignKey(
        WhatsAppAccount,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "account")]
        ordering = ["-assigned_at"]
        verbose_name = "Account Assignment"
        verbose_name_plural = "Account Assignments"

    def __str__(self) -> str:
        return f"{self.user.username} → {self.account.name}"
