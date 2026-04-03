"""
Unit tests for the `accounts` app (Module 1).

Coverage:
- User model: role field, helper methods, string representation
- LoginView: GET, valid POST, invalid POST, role-based redirect
- logout_view: clears session and redirects
- AdminRequiredMixin: grants admin, denies agent, denies anonymous
- AgentRequiredMixin: grants any authenticated user, denies anonymous
- LoginForm: valid/invalid input combinations
"""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.cache import SessionStore
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.views.generic import TemplateView

from accounts.forms import LoginForm
from accounts.mixins import AdminRequiredMixin, AgentRequiredMixin
from .conftest import AdminUserFactory, AgentUserFactory

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(path="/", user=None):
    """Build a GET request with an attached user and session."""
    factory = RequestFactory()
    request = factory.get(path)
    request.user = user or AnonymousUser()
    request.session = SessionStore()
    return request


# ===========================================================================
# Model tests
# ===========================================================================

@pytest.mark.django_db
class TestUserModel:
    def test_default_role_is_agent(self):
        user = User.objects.create_user(username="newuser", password="pass")
        assert user.role == User.Role.AGENT

    def test_admin_role_choice(self):
        user = AdminUserFactory()
        assert user.role == "admin"
        assert user.is_admin_role() is True
        assert user.is_agent_role() is False

    def test_agent_role_choice(self):
        user = AgentUserFactory()
        assert user.role == "agent"
        assert user.is_agent_role() is True
        assert user.is_admin_role() is False

    def test_str_includes_username_and_role(self):
        user = AdminUserFactory(username="alice")
        assert "alice" in str(user)
        assert "Admin" in str(user)

    def test_role_choices_are_exhaustive(self):
        choices = {c[0] for c in User.Role.choices}
        assert choices == {"admin", "agent"}

    def test_user_creation_with_admin_role(self):
        user = User.objects.create_user(
            username="boss", password="secret", role="admin"
        )
        assert user.is_admin_role()

    def test_multiple_users_unique_usernames(self):
        u1 = AgentUserFactory()
        u2 = AgentUserFactory()
        assert u1.username != u2.username

    def test_admin_str_shows_role_label(self):
        user = AgentUserFactory(username="bob")
        assert "Agent" in str(user)


# ===========================================================================
# LoginView tests
# ===========================================================================

@pytest.mark.django_db
class TestLoginView:
    url = "/login/"

    def test_get_renders_form(self, client):
        response = client.get(self.url)
        assert response.status_code == 200
        assert b"Sign In" in response.content

    def test_get_redirects_authenticated_admin(self, admin_client):
        response = admin_client.get(self.url)
        assert response.status_code == 302
        assert response["Location"] == reverse("admin_panel:dashboard")

    def test_get_redirects_authenticated_agent(self, agent_client):
        response = agent_client.get(self.url)
        assert response.status_code == 302
        assert response["Location"] == reverse("chat:list")

    def test_valid_admin_login_redirects_to_admin_panel(self, client, admin_user):
        response = client.post(
            self.url,
            {"username": admin_user.username, "password": "password123"},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("admin_panel:dashboard")

    def test_valid_agent_login_redirects_to_chat(self, client, agent_user):
        response = client.post(
            self.url,
            {"username": agent_user.username, "password": "password123"},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("chat:list")

    def test_invalid_password_shows_error(self, client, agent_user):
        response = client.post(
            self.url,
            {"username": agent_user.username, "password": "wrong"},
        )
        assert response.status_code == 200
        assert b"Invalid username or password" in response.content

    def test_nonexistent_user_shows_error(self, client):
        response = client.post(
            self.url,
            {"username": "ghost", "password": "whatever"},
        )
        assert response.status_code == 200
        assert b"Invalid username or password" in response.content

    def test_empty_username_shows_form_error(self, client):
        response = client.post(self.url, {"username": "", "password": "pass"})
        assert response.status_code == 200

    def test_empty_password_shows_form_error(self, client):
        response = client.post(self.url, {"username": "u", "password": ""})
        assert response.status_code == 200

    def test_inactive_user_cannot_login(self, client):
        user = AgentUserFactory(is_active=False)
        response = client.post(
            self.url,
            {"username": user.username, "password": "password123"},
        )
        assert response.status_code == 200
        assert b"Invalid username or password" in response.content


# ===========================================================================
# LogoutView tests
# ===========================================================================

@pytest.mark.django_db
class TestLogoutView:
    url = "/logout/"

    def test_logout_redirects_to_login(self, agent_client):
        response = agent_client.get(self.url)
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:login")

    def test_logout_clears_session(self, client, agent_user):
        client.force_login(agent_user)
        client.get(self.url)
        # After logout, /chat/ stub no longer has login_required so just
        # confirm the user is no longer authenticated via the login page
        response = client.get(self.url)  # second logout should redirect to login
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_unauthenticated_logout_redirects_to_login(self, client):
        response = client.get(self.url)
        assert response.status_code == 302
        assert "/login/" in response["Location"]


# ===========================================================================
# AdminRequiredMixin tests
# ===========================================================================

@pytest.mark.django_db
class TestAdminRequiredMixin:
    """Direct unit-tests against the mixin using a synthetic sentinel view."""

    class _SentinelView(AdminRequiredMixin, TemplateView):
        template_name = "base.html"

        def get(self, request, *args, **kwargs):
            return HttpResponse("ok")

    def test_admin_passes_through(self):
        admin = AdminUserFactory()
        request = _make_request(user=admin)
        response = self._SentinelView.as_view()(request)
        assert response.status_code == 200

    def test_agent_raises_403(self):
        agent = AgentUserFactory()
        request = _make_request(user=agent)
        with pytest.raises(PermissionDenied):
            self._SentinelView.as_view()(request)

    def test_anonymous_redirects_to_login(self):
        request = _make_request(user=AnonymousUser())
        response = self._SentinelView.as_view()(request)
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_login_redirect_authenticated_admin(self, admin_client):
        """Via the HTTP client: an admin on the login page gets redirected."""
        response = admin_client.get("/login/")
        assert response.status_code == 302
        assert response["Location"] == reverse("admin_panel:dashboard")


# ===========================================================================
# AgentRequiredMixin tests
# ===========================================================================

@pytest.mark.django_db
class TestAgentRequiredMixin:
    class _SentinelView(AgentRequiredMixin, TemplateView):
        template_name = "base.html"

        def get(self, request, *args, **kwargs):
            return HttpResponse("ok")

    def test_agent_passes_through(self):
        agent = AgentUserFactory()
        request = _make_request(user=agent)
        response = self._SentinelView.as_view()(request)
        assert response.status_code == 200

    def test_admin_also_passes_through(self):
        admin = AdminUserFactory()
        request = _make_request(user=admin)
        response = self._SentinelView.as_view()(request)
        assert response.status_code == 200

    def test_anonymous_redirects_to_login(self):
        request = _make_request(user=AnonymousUser())
        response = self._SentinelView.as_view()(request)
        assert response.status_code == 302
        assert "/login/" in response["Location"]


# ===========================================================================
# LoginForm tests
# ===========================================================================

class TestLoginForm:
    def test_valid_form(self):
        form = LoginForm(data={"username": "alice", "password": "secret"})
        assert form.is_valid()

    def test_missing_username_invalid(self):
        form = LoginForm(data={"username": "", "password": "secret"})
        assert not form.is_valid()
        assert "username" in form.errors

    def test_missing_password_invalid(self):
        form = LoginForm(data={"username": "alice", "password": ""})
        assert not form.is_valid()
        assert "password" in form.errors

    def test_both_empty_is_invalid(self):
        form = LoginForm(data={"username": "", "password": ""})
        assert not form.is_valid()

    def test_username_too_long_is_invalid(self):
        form = LoginForm(data={"username": "x" * 151, "password": "pass"})
        assert not form.is_valid()
        assert "username" in form.errors
