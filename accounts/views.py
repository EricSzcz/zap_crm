from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views import View

from .forms import LoginForm


class LoginView(View):
    template_name = "accounts/login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return self._redirect_by_role(request.user)
        return render(request, self.template_name, {"form": LoginForm()})

    def post(self, request):
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
            )
            if user is not None:
                login(request, user)
                return self._redirect_by_role(user)
            form.add_error(None, "Invalid username or password.")
        return render(request, self.template_name, {"form": form})

    @staticmethod
    def _redirect_by_role(user):
        if user.is_admin_role():
            return redirect("admin_panel:dashboard")
        return redirect("chat:list")


@login_required
def logout_view(request):
    logout(request)
    return redirect("accounts:login")
