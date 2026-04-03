"""
Unit tests for the `whatsapp_config` app (Module 2).

Coverage:
- EncryptedTextField: encrypts at rest, transparent on read, key rotation
- WhatsAppAccount model: CRUD, str, get_api_headers
- AccountAssignment model: unique_together, str
- WhatsAppAccountForm: valid/invalid input, edit mode (blank token retention)
- AccountAssignmentForm: queryset filtering
- AgentCreateForm: saves with role=agent
- Admin panel views: access control, CRUD operations, assignment management
"""
import pytest
import factory
from factory.django import DjangoModelFactory
from django.contrib.auth import get_user_model
from django.urls import reverse

from whatsapp_config.models import AccountAssignment, WhatsAppAccount
from .conftest import AdminUserFactory, AgentUserFactory

User = get_user_model()


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

class WhatsAppAccountFactory(DjangoModelFactory):
    class Meta:
        model = WhatsAppAccount
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Test Account {n}")
    phone_number_id = factory.Sequence(lambda n: f"1234567890{n}")
    api_token = "test-api-token-secret"
    webhook_verify_token = "verify-me"
    is_active = True


class AccountAssignmentFactory(DjangoModelFactory):
    class Meta:
        model = AccountAssignment
        skip_postgeneration_save = True

    user = factory.SubFactory(AgentUserFactory)
    account = factory.SubFactory(WhatsAppAccountFactory)


# ---------------------------------------------------------------------------
# EncryptedTextField tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEncryptedTextField:
    def test_value_stored_encrypted_in_db(self):
        """The raw DB value must not equal the plaintext token."""
        from django.db import connection
        account = WhatsAppAccountFactory()
        account.save()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT api_token FROM whatsapp_config_whatsappaccount WHERE id = %s",
                [account.pk],
            )
            raw = cursor.fetchone()[0]
        assert raw != "test-api-token-secret"
        # Fernet tokens start with 'gA'
        assert raw.startswith("gA")

    def test_value_transparently_decrypted_on_read(self):
        account = WhatsAppAccountFactory()
        account.save()
        fetched = WhatsAppAccount.objects.get(pk=account.pk)
        assert fetched.api_token == "test-api-token-secret"

    def test_empty_string_is_encrypted(self):
        """An empty-string token gets encrypted and decrypted transparently."""
        account = WhatsAppAccountFactory(api_token="")
        account.save()
        fetched = WhatsAppAccount.objects.get(pk=account.pk)
        assert fetched.api_token == ""

    def test_key_rotation_still_decrypts(self):
        """After adding a new key at position 0, old ciphertext is still readable."""
        from cryptography.fernet import Fernet
        from django.test import override_settings

        new_key = Fernet.generate_key().decode()
        account = WhatsAppAccountFactory(api_token="rotation-test")
        account.save()

        from django.conf import settings
        old_keys = settings.FERNET_KEYS
        with override_settings(FERNET_KEYS=[new_key] + list(old_keys)):
            fetched = WhatsAppAccount.objects.get(pk=account.pk)
            assert fetched.api_token == "rotation-test"


# ---------------------------------------------------------------------------
# WhatsAppAccount model tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWhatsAppAccountModel:
    def test_create_and_retrieve(self):
        account = WhatsAppAccountFactory()
        account.save()
        assert WhatsAppAccount.objects.filter(pk=account.pk).exists()

    def test_str_active(self):
        account = WhatsAppAccountFactory(name="Acme", phone_number_id="111", is_active=True)
        account.save()
        assert "Acme" in str(account)
        assert "active" in str(account)

    def test_str_inactive(self):
        account = WhatsAppAccountFactory(name="Acme", phone_number_id="222", is_active=False)
        account.save()
        assert "inactive" in str(account)

    def test_phone_number_id_is_unique(self):
        from django.db import IntegrityError
        WhatsAppAccountFactory(phone_number_id="unique-id").save()
        with pytest.raises(IntegrityError):
            WhatsAppAccountFactory(phone_number_id="unique-id").save()

    def test_get_api_headers_returns_bearer_token(self):
        account = WhatsAppAccountFactory()
        account.save()
        headers = account.get_api_headers()
        assert headers["Authorization"] == "Bearer test-api-token-secret"
        assert headers["Content-Type"] == "application/json"

    def test_default_is_active_true(self):
        account = WhatsAppAccountFactory()
        account.save()
        assert account.is_active is True

    def test_ordering_newest_first(self):
        a1 = WhatsAppAccountFactory(phone_number_id="p1")
        a1.save()
        a2 = WhatsAppAccountFactory(phone_number_id="p2")
        a2.save()
        accounts = list(WhatsAppAccount.objects.all())
        assert accounts[0].pk == a2.pk


# ---------------------------------------------------------------------------
# AccountAssignment model tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAccountAssignmentModel:
    def test_create_assignment(self):
        assignment = AccountAssignmentFactory()
        assignment.save()
        assert AccountAssignment.objects.filter(pk=assignment.pk).exists()

    def test_str(self):
        agent = AgentUserFactory(username="charlie")
        account = WhatsAppAccountFactory(name="BizWA", phone_number_id="p99")
        account.save()
        assignment = AccountAssignment(user=agent, account=account)
        assignment.save()
        assert "charlie" in str(assignment)
        assert "BizWA" in str(assignment)

    def test_unique_together_enforced(self):
        from django.db import IntegrityError
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory(phone_number_id="px1")
        account.save()
        AccountAssignment.objects.create(user=agent, account=account)
        with pytest.raises(IntegrityError):
            AccountAssignment.objects.create(user=agent, account=account)

    def test_agent_can_be_assigned_to_multiple_accounts(self):
        agent = AgentUserFactory()
        a1 = WhatsAppAccountFactory(phone_number_id="px2")
        a1.save()
        a2 = WhatsAppAccountFactory(phone_number_id="px3")
        a2.save()
        AccountAssignment.objects.create(user=agent, account=a1)
        AccountAssignment.objects.create(user=agent, account=a2)
        assert AccountAssignment.objects.filter(user=agent).count() == 2

    def test_account_can_have_multiple_agents(self):
        account = WhatsAppAccountFactory(phone_number_id="px4")
        account.save()
        a1 = AgentUserFactory()
        a2 = AgentUserFactory()
        AccountAssignment.objects.create(user=a1, account=account)
        AccountAssignment.objects.create(user=a2, account=account)
        assert AccountAssignment.objects.filter(account=account).count() == 2


# ---------------------------------------------------------------------------
# Form tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWhatsAppAccountForm:
    def _valid_data(self, **overrides):
        data = {
            "name": "Test Biz",
            "phone_number_id": "999000111",
            "api_token": "supersecret",
            "webhook_verify_token": "mytoken",
            "is_active": True,
        }
        data.update(overrides)
        return data

    def test_valid_create_form(self):
        from whatsapp_config.forms import WhatsAppAccountForm
        form = WhatsAppAccountForm(data=self._valid_data())
        assert form.is_valid(), form.errors

    def test_missing_name_invalid(self):
        from whatsapp_config.forms import WhatsAppAccountForm
        form = WhatsAppAccountForm(data=self._valid_data(name=""))
        assert not form.is_valid()
        assert "name" in form.errors

    def test_missing_token_invalid_on_create(self):
        from whatsapp_config.forms import WhatsAppAccountForm
        form = WhatsAppAccountForm(data=self._valid_data(api_token=""))
        assert not form.is_valid()
        assert "api_token" in form.errors

    def test_blank_token_on_edit_retains_existing(self):
        """When editing, leaving api_token blank keeps the saved value."""
        from whatsapp_config.forms import WhatsAppAccountForm
        account = WhatsAppAccountFactory()
        account.save()
        data = {
            "name": account.name,
            "phone_number_id": account.phone_number_id,
            "api_token": "",  # blank — should keep existing
            "webhook_verify_token": account.webhook_verify_token,
            "is_active": account.is_active,
        }
        form = WhatsAppAccountForm(data=data, instance=account, is_edit=True)
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.api_token == "test-api-token-secret"

    def test_new_token_on_edit_replaces_existing(self):
        from whatsapp_config.forms import WhatsAppAccountForm
        account = WhatsAppAccountFactory()
        account.save()
        data = {
            "name": account.name,
            "phone_number_id": account.phone_number_id,
            "api_token": "brand-new-token",
            "webhook_verify_token": account.webhook_verify_token,
            "is_active": account.is_active,
        }
        form = WhatsAppAccountForm(data=data, instance=account, is_edit=True)
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.api_token == "brand-new-token"


@pytest.mark.django_db
class TestAccountAssignmentForm:
    def test_user_queryset_only_active_agents(self):
        from whatsapp_config.forms import AccountAssignmentForm
        AdminUserFactory()   # should not appear
        inactive_agent = AgentUserFactory(is_active=False)
        active_agent = AgentUserFactory(is_active=True)
        form = AccountAssignmentForm()
        qs = form.fields["user"].queryset
        assert active_agent in qs
        assert inactive_agent not in qs

    def test_account_queryset_only_active(self):
        from whatsapp_config.forms import AccountAssignmentForm
        active = WhatsAppAccountFactory(phone_number_id="pa1")
        active.save()
        inactive = WhatsAppAccountFactory(phone_number_id="pa2", is_active=False)
        inactive.save()
        form = AccountAssignmentForm()
        qs = form.fields["account"].queryset
        assert active in qs
        assert inactive not in qs


@pytest.mark.django_db
class TestAgentCreateForm:
    def test_saves_with_agent_role(self):
        from whatsapp_config.user_forms import AgentCreateForm
        form = AgentCreateForm(data={
            "username": "newagent",
            "email": "a@b.com",
            "first_name": "New",
            "last_name": "Agent",
            "password1": "Str0ng!Pass",
            "password2": "Str0ng!Pass",
        })
        assert form.is_valid(), form.errors
        user = form.save()
        assert user.role == "agent"
        assert user.pk is not None


# ---------------------------------------------------------------------------
# Admin panel view tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAdminPanelAccessControl:
    """Every admin panel URL must block non-admins."""

    protected_urls = [
        "admin_panel:dashboard",
        "admin_panel:account_list",
        "admin_panel:account_create",
        "admin_panel:user_list",
        "admin_panel:user_create",
        "admin_panel:assignment_list",
        "admin_panel:conversation_monitor",
    ]

    def test_anonymous_redirected_to_login(self, client):
        for name in self.protected_urls:
            response = client.get(reverse(name))
            assert response.status_code == 302, f"{name} should redirect anonymous"
            assert "/login/" in response["Location"]

    def test_agent_gets_403(self, agent_client):
        for name in self.protected_urls:
            response = agent_client.get(reverse(name))
            assert response.status_code == 403, f"{name} should 403 agents"

    def test_admin_gets_200(self, admin_client):
        for name in self.protected_urls:
            response = admin_client.get(reverse(name))
            assert response.status_code == 200, f"{name} should return 200 for admin"


@pytest.mark.django_db
class TestAccountCRUDViews:
    def test_dashboard_shows_counts(self, admin_client):
        WhatsAppAccountFactory(phone_number_id="d1").save()
        WhatsAppAccountFactory(phone_number_id="d2", is_active=False).save()
        response = admin_client.get(reverse("admin_panel:dashboard"))
        assert response.status_code == 200

    def test_account_list_shows_accounts(self, admin_client):
        account = WhatsAppAccountFactory(phone_number_id="L1")
        account.save()
        response = admin_client.get(reverse("admin_panel:account_list"))
        assert response.status_code == 200
        assert account.name.encode() in response.content

    def test_account_create_get(self, admin_client):
        response = admin_client.get(reverse("admin_panel:account_create"))
        assert response.status_code == 200
        assert b"form" in response.content.lower()

    def test_account_create_post_valid(self, admin_client):
        response = admin_client.post(
            reverse("admin_panel:account_create"),
            {
                "name": "Prod Account",
                "phone_number_id": "555666777",
                "api_token": "secret-tok",
                "webhook_verify_token": "wh-secret",
                "is_active": True,
            },
        )
        assert response.status_code == 302
        assert WhatsAppAccount.objects.filter(phone_number_id="555666777").exists()

    def test_account_create_post_invalid(self, admin_client):
        response = admin_client.post(
            reverse("admin_panel:account_create"),
            {"name": "", "phone_number_id": "", "api_token": ""},
        )
        assert response.status_code == 200  # re-renders form

    def test_account_edit_get(self, admin_client):
        account = WhatsAppAccountFactory(phone_number_id="E1")
        account.save()
        response = admin_client.get(reverse("admin_panel:account_edit", args=[account.pk]))
        assert response.status_code == 200

    def test_account_edit_post(self, admin_client):
        account = WhatsAppAccountFactory(phone_number_id="E2")
        account.save()
        response = admin_client.post(
            reverse("admin_panel:account_edit", args=[account.pk]),
            {
                "name": "Updated Name",
                "phone_number_id": "E2",
                "api_token": "",  # edit mode — keep existing
                "webhook_verify_token": "wh",
                "is_active": True,
            },
        )
        assert response.status_code == 302
        account.refresh_from_db()
        assert account.name == "Updated Name"

    def test_account_delete_get(self, admin_client):
        account = WhatsAppAccountFactory(phone_number_id="D1")
        account.save()
        response = admin_client.get(reverse("admin_panel:account_delete", args=[account.pk]))
        assert response.status_code == 200

    def test_account_delete_post(self, admin_client):
        account = WhatsAppAccountFactory(phone_number_id="D2")
        account.save()
        pk = account.pk
        response = admin_client.post(reverse("admin_panel:account_delete", args=[pk]))
        assert response.status_code == 302
        assert not WhatsAppAccount.objects.filter(pk=pk).exists()

    def test_account_toggle_active(self, admin_client):
        account = WhatsAppAccountFactory(phone_number_id="T1", is_active=True)
        account.save()
        admin_client.post(reverse("admin_panel:account_toggle", args=[account.pk]))
        account.refresh_from_db()
        assert account.is_active is False

    def test_account_toggle_inactive_to_active(self, admin_client):
        account = WhatsAppAccountFactory(phone_number_id="T2", is_active=False)
        account.save()
        admin_client.post(reverse("admin_panel:account_toggle", args=[account.pk]))
        account.refresh_from_db()
        assert account.is_active is True


@pytest.mark.django_db
class TestUserManagementViews:
    def test_user_list_shows_agents(self, admin_client):
        agent = AgentUserFactory()
        response = admin_client.get(reverse("admin_panel:user_list"))
        assert response.status_code == 200
        assert agent.username.encode() in response.content

    def test_create_agent_post(self, admin_client):
        response = admin_client.post(
            reverse("admin_panel:user_create"),
            {
                "username": "fresh_agent",
                "email": "fa@example.com",
                "first_name": "Fresh",
                "last_name": "Agent",
                "password1": "Str0ng!Pass",
                "password2": "Str0ng!Pass",
            },
        )
        assert response.status_code == 302
        user = User.objects.get(username="fresh_agent")
        assert user.role == "agent"

    def test_toggle_agent_active(self, admin_client):
        agent = AgentUserFactory(is_active=True)
        admin_client.post(reverse("admin_panel:user_toggle", args=[agent.pk]))
        agent.refresh_from_db()
        assert agent.is_active is False


@pytest.mark.django_db
class TestAssignmentViews:
    def test_assignment_list_get(self, admin_client):
        response = admin_client.get(reverse("admin_panel:assignment_list"))
        assert response.status_code == 200

    def test_create_assignment_post(self, admin_client):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory(phone_number_id="asgn1")
        account.save()
        response = admin_client.post(
            reverse("admin_panel:assignment_list"),
            {"user": agent.pk, "account": account.pk},
        )
        assert response.status_code == 302
        assert AccountAssignment.objects.filter(user=agent, account=account).exists()

    def test_duplicate_assignment_does_not_crash(self, admin_client):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory(phone_number_id="asgn2")
        account.save()
        AccountAssignment.objects.create(user=agent, account=account)
        # Second POST should not 500
        response = admin_client.post(
            reverse("admin_panel:assignment_list"),
            {"user": agent.pk, "account": account.pk},
        )
        assert response.status_code == 302

    def test_delete_assignment(self, admin_client):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory(phone_number_id="asgn3")
        account.save()
        assignment = AccountAssignment.objects.create(user=agent, account=account)
        response = admin_client.post(
            reverse("admin_panel:assignment_delete", args=[assignment.pk])
        )
        assert response.status_code == 302
        assert not AccountAssignment.objects.filter(pk=assignment.pk).exists()


@pytest.mark.django_db
class TestConversationMonitorView:
    def test_monitor_renders(self, admin_client):
        response = admin_client.get(reverse("admin_panel:conversation_monitor"))
        assert response.status_code == 200

    def test_monitor_filters_by_status(self, admin_client):
        response = admin_client.get(
            reverse("admin_panel:conversation_monitor") + "?status=open"
        )
        assert response.status_code == 200
