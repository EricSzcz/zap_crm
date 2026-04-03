"""
conversations/models.py

Three core models: Contact, Conversation, Message.

Queryset-level security
-----------------------
All querysets expose a `.for_agent(user)` classmethod that narrows results
to only the data the given agent is allowed to see (conversations reachable
through their AccountAssignment records).  Views MUST use this method
instead of `.all()` for any agent-facing query.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------

class ContactQuerySet(models.QuerySet):
    def for_agent(self, user):
        """Return contacts visible to this user.
        Admin users can see all contacts; agents only see contacts
        reachable through their AccountAssignment records.
        """
        if getattr(user, "role", None) == "admin":
            return self.all()
        return self.filter(
            conversations__whatsapp_account__assignments__user=user
        ).distinct()


class Contact(models.Model):
    phone_number = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ContactQuerySet.as_manager()

    class Meta:
        ordering = ["phone_number"]

    def __str__(self) -> str:
        return self.name or self.phone_number

    @property
    def display_name(self) -> str:
        """Human-readable label: prefer name, fall back to phone number."""
        return self.name or self.phone_number


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

class ConversationQuerySet(models.QuerySet):
    def for_agent(self, user):
        """
        Return conversations accessible to this user.
        Admin users can see all conversations; agents only see conversations
        for WhatsApp accounts they are assigned to.
        This is the primary security gate — every agent-facing view must use it.
        """
        if getattr(user, "role", None) == "admin":
            return self.all()
        return self.filter(
            whatsapp_account__assignments__user=user
        ).distinct()

    def open(self):
        return self.filter(status=Conversation.Status.OPEN)

    def closed(self):
        return self.filter(status=Conversation.Status.CLOSED)


class Conversation(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    whatsapp_account = models.ForeignKey(
        "whatsapp_config.WhatsAppAccount",
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    assigned_agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conversations",
        limit_choices_to={"role": "agent"},
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ConversationQuerySet.as_manager()

    class Meta:
        ordering = ["-last_message_at"]
        indexes = [
            models.Index(fields=["whatsapp_account", "status"]),
        ]

    def __str__(self) -> str:
        return f"Conv#{self.pk} with {self.contact}"

    def touch(self):
        """Update last_message_at to now and save only that field."""
        self.last_message_at = timezone.now()
        self.save(update_fields=["last_message_at"])

    def close(self):
        self.status = self.Status.CLOSED
        self.save(update_fields=["status"])

    def reopen(self):
        self.status = self.Status.OPEN
        self.save(update_fields=["status"])


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

class MessageQuerySet(models.QuerySet):
    def for_agent(self, user):
        """Return messages in conversations this user can access.
        Admin users can see all messages; agents only see messages in
        conversations reachable through their AccountAssignment records.
        """
        if getattr(user, "role", None) == "admin":
            return self.all()
        return self.filter(
            conversation__whatsapp_account__assignments__user=user
        ).distinct()

    def inbound(self):
        return self.filter(direction=Message.Direction.INBOUND)

    def outbound(self):
        return self.filter(direction=Message.Direction.OUTBOUND)


class Message(models.Model):
    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    content = models.TextField()
    direction = models.CharField(max_length=10, choices=Direction.choices, db_index=True)
    # Nullable: outbound messages get a whatsapp_message_id only after Meta confirms delivery
    whatsapp_message_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MessageQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"[{self.direction}] {self.content[:50]}"

    def mark_sent(self, whatsapp_message_id: str):
        self.status = self.Status.SENT
        self.whatsapp_message_id = whatsapp_message_id
        self.save(update_fields=["status", "whatsapp_message_id"])

    def mark_delivered(self):
        self.status = self.Status.DELIVERED
        self.save(update_fields=["status"])

    def mark_read(self):
        self.status = self.Status.READ
        self.save(update_fields=["status"])

    def mark_failed(self):
        self.status = self.Status.FAILED
        self.save(update_fields=["status"])
