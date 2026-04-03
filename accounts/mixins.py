from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


class AdminRequiredMixin(LoginRequiredMixin):
    """Allow access only to users with role == 'admin'."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.is_admin_role():
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class AgentRequiredMixin(LoginRequiredMixin):
    """Allow access only to authenticated agents (any role may enter the chat,
    but queryset-level security still applies)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)
