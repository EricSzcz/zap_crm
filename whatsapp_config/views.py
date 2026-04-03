"""
Admin-only views for the custom admin panel.

All views are protected by AdminRequiredMixin — any unauthenticated request
is redirected to /login/ and any authenticated non-admin gets a 403.
"""
import json

import requests
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from accounts.mixins import AdminRequiredMixin
from conversations.models import Conversation, Message

from .forms import AccountAssignmentForm, WhatsAppAccountForm
from .models import AccountAssignment, WhatsAppAccount

User = get_user_model()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardView(AdminRequiredMixin, View):
    template_name = "admin_panel/dashboard.html"

    def get(self, request):
        from django.utils import timezone
        today = timezone.now().date()
        context = {
            "total_accounts": WhatsAppAccount.objects.count(),
            "active_accounts": WhatsAppAccount.objects.filter(is_active=True).count(),
            "total_agents": User.objects.filter(role="agent", is_active=True).count(),
            "open_conversations": Conversation.objects.filter(status="open").count(),
            "messages_today": Message.objects.filter(created_at__date=today).count(),
        }
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# WhatsApp Account CRUD
# ---------------------------------------------------------------------------

class AccountListView(AdminRequiredMixin, View):
    template_name = "admin_panel/accounts/list.html"

    def get(self, request):
        accounts = WhatsAppAccount.objects.prefetch_related("assignments__user")
        return render(request, self.template_name, {"accounts": accounts})


class AccountCreateView(AdminRequiredMixin, View):
    template_name = "admin_panel/accounts/form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": WhatsAppAccountForm(), "action": "Create"})

    def post(self, request):
        form = WhatsAppAccountForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "WhatsApp account created successfully.")
            return redirect("admin_panel:account_list")
        return render(request, self.template_name, {"form": form, "action": "Create"})


class AccountEditView(AdminRequiredMixin, View):
    template_name = "admin_panel/accounts/form.html"

    def _get_account(self, pk):
        return get_object_or_404(WhatsAppAccount, pk=pk)

    def get(self, request, pk):
        account = self._get_account(pk)
        form = WhatsAppAccountForm(instance=account, is_edit=True)
        return render(request, self.template_name, {"form": form, "account": account, "action": "Edit"})

    def post(self, request, pk):
        account = self._get_account(pk)
        form = WhatsAppAccountForm(request.POST, instance=account, is_edit=True)
        if form.is_valid():
            form.save()
            messages.success(request, "WhatsApp account updated.")
            return redirect("admin_panel:account_list")
        return render(request, self.template_name, {"form": form, "account": account, "action": "Edit"})


class AccountDeleteView(AdminRequiredMixin, View):
    template_name = "admin_panel/accounts/confirm_delete.html"

    def get(self, request, pk):
        account = get_object_or_404(WhatsAppAccount, pk=pk)
        return render(request, self.template_name, {"account": account})

    def post(self, request, pk):
        account = get_object_or_404(WhatsAppAccount, pk=pk)
        account.delete()
        messages.success(request, f'Account "{account.name}" deleted.')
        return redirect("admin_panel:account_list")


class AccountToggleActiveView(AdminRequiredMixin, View):
    """Quick toggle for is_active without going through the full edit form."""

    def post(self, request, pk):
        account = get_object_or_404(WhatsAppAccount, pk=pk)
        account.is_active = not account.is_active
        account.save(update_fields=["is_active"])
        state = "activated" if account.is_active else "deactivated"
        messages.success(request, f'Account "{account.name}" {state}.')
        return redirect("admin_panel:account_list")


class AccountTestConnectionView(AdminRequiredMixin, View):
    """
    Hits the Meta Graph API to verify the stored token is valid.
    Returns a simple JSON response consumed by the frontend.
    """

    def post(self, request, pk):
        account = get_object_or_404(WhatsAppAccount, pk=pk)
        url = f"https://graph.facebook.com/v19.0/{account.phone_number_id}"
        try:
            resp = requests.get(url, headers=account.get_api_headers(), timeout=10)
            ok = resp.status_code == 200
            detail = resp.json() if ok else resp.text[:200]
        except requests.RequestException as exc:
            ok = False
            detail = str(exc)

        from django.http import JsonResponse
        return JsonResponse({"success": ok, "detail": detail})


# ---------------------------------------------------------------------------
# Agent / User management
# ---------------------------------------------------------------------------

class UserListView(AdminRequiredMixin, View):
    template_name = "admin_panel/users/list.html"

    def get(self, request):
        agents = User.objects.filter(role="agent").order_by("username")
        return render(request, self.template_name, {"agents": agents})


class UserCreateView(AdminRequiredMixin, View):
    template_name = "admin_panel/users/form.html"

    def get(self, request):
        from .user_forms import AgentCreateForm
        return render(request, self.template_name, {"form": AgentCreateForm(), "action": "Create Agent"})

    def post(self, request):
        from .user_forms import AgentCreateForm
        form = AgentCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Agent created.")
            return redirect("admin_panel:user_list")
        return render(request, self.template_name, {"form": form, "action": "Create Agent"})


class UserToggleActiveView(AdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk, role="agent")
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        state = "activated" if user.is_active else "deactivated"
        messages.success(request, f"Agent {user.username} {state}.")
        return redirect("admin_panel:user_list")


# ---------------------------------------------------------------------------
# Assignment management
# ---------------------------------------------------------------------------

class AssignmentListView(AdminRequiredMixin, View):
    template_name = "admin_panel/assignments/list.html"

    def get(self, request):
        assignments = AccountAssignment.objects.select_related("user", "account")
        form = AccountAssignmentForm()
        return render(request, self.template_name, {"assignments": assignments, "form": form})

    def post(self, request):
        form = AccountAssignmentForm(request.POST)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Assignment created.")
            except Exception:
                messages.error(request, "This agent is already assigned to that account.")
        else:
            messages.error(request, "Invalid assignment data.")
        return redirect("admin_panel:assignment_list")


class AssignmentDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        assignment = get_object_or_404(AccountAssignment, pk=pk)
        assignment.delete()
        messages.success(request, "Assignment removed.")
        return redirect("admin_panel:assignment_list")


# ---------------------------------------------------------------------------
# Conversation monitor
# ---------------------------------------------------------------------------

class ConversationMonitorView(AdminRequiredMixin, View):
    template_name = "admin_panel/conversations/monitor.html"

    def get(self, request):
        qs = Conversation.objects.select_related(
            "contact", "whatsapp_account", "assigned_agent"
        ).order_by("-last_message_at")

        account_id = request.GET.get("account")
        agent_id = request.GET.get("agent")
        status = request.GET.get("status")

        if account_id:
            qs = qs.filter(whatsapp_account_id=account_id)
        if agent_id:
            qs = qs.filter(assigned_agent_id=agent_id)
        if status:
            qs = qs.filter(status=status)

        context = {
            "conversations": qs,
            "accounts": WhatsAppAccount.objects.all(),
            "agents": User.objects.filter(role="agent", is_active=True),
            "selected_account": account_id,
            "selected_agent": agent_id,
            "selected_status": status,
        }
        return render(request, self.template_name, context)
