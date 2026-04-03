"""Shared fixtures and factories for the test suite."""
import pytest
import factory
from factory.django import DjangoModelFactory
from django.contrib.auth import get_user_model

User = get_user_model()

# Lazy imports so the factories are only resolved when the apps are ready
def _get_whatsapp_account_model():
    from whatsapp_config.models import WhatsAppAccount
    return WhatsAppAccount

def _get_account_assignment_model():
    from whatsapp_config.models import AccountAssignment
    return AccountAssignment

def _get_contact_model():
    from conversations.models import Contact
    return Contact

def _get_conversation_model():
    from conversations.models import Conversation
    return Conversation

def _get_message_model():
    from conversations.models import Message
    return Message


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

class UserFactory(DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user_{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    password = factory.PostGenerationMethodCall("set_password", "password123")
    role = "agent"
    is_active = True

    @classmethod
    def _after_postgeneration(cls, instance, create, results=None):
        """Re-save after set_password so the hashed password is persisted to DB.
        Without this, force_login stores a session hash that doesn't match the DB row."""
        if create and results:
            instance.save(update_fields=["password"])


class AdminUserFactory(UserFactory):
    role = "admin"
    username = factory.Sequence(lambda n: f"admin_{n}")


class AgentUserFactory(UserFactory):
    role = "agent"
    username = factory.Sequence(lambda n: f"agent_{n}")


class WhatsAppAccountFactory(DjangoModelFactory):
    class Meta:
        model = "whatsapp_config.WhatsAppAccount"
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"WA Account {n}")
    phone_number_id = factory.Sequence(lambda n: f"10000000{n:04d}")
    api_token = "test-token"
    webhook_verify_token = "verify-token"
    is_active = True


class AccountAssignmentFactory(DjangoModelFactory):
    class Meta:
        model = "whatsapp_config.AccountAssignment"
        skip_postgeneration_save = True

    user = factory.SubFactory(AgentUserFactory)
    account = factory.SubFactory(WhatsAppAccountFactory)


class ContactFactory(DjangoModelFactory):
    class Meta:
        model = "conversations.Contact"
        skip_postgeneration_save = True

    phone_number = factory.Sequence(lambda n: f"+1555000{n:04d}")
    name = factory.Sequence(lambda n: f"Contact {n}")


class ConversationFactory(DjangoModelFactory):
    class Meta:
        model = "conversations.Conversation"
        skip_postgeneration_save = True

    contact = factory.SubFactory(ContactFactory)
    whatsapp_account = factory.SubFactory(WhatsAppAccountFactory)
    assigned_agent = None
    status = "open"


class MessageFactory(DjangoModelFactory):
    class Meta:
        model = "conversations.Message"
        skip_postgeneration_save = True

    conversation = factory.SubFactory(ConversationFactory)
    content = factory.Sequence(lambda n: f"Message content {n}")
    direction = "inbound"
    whatsapp_message_id = factory.Sequence(lambda n: f"wamid.{n:010d}")
    status = "sent"


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_user(db):
    return AdminUserFactory()


@pytest.fixture
def agent_user(db):
    return AgentUserFactory()


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def agent_client(client, agent_user):
    client.force_login(agent_user)
    return client
