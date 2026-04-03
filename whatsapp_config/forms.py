from django import forms
from django.contrib.auth import get_user_model

from .models import AccountAssignment, WhatsAppAccount

User = get_user_model()


class WhatsAppAccountForm(forms.ModelForm):
    class Meta:
        model = WhatsAppAccount
        fields = ["name", "phone_number_id", "api_token", "webhook_verify_token", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "phone_number_id": forms.TextInput(attrs={"class": "form-control"}),
            "api_token": forms.PasswordInput(
                attrs={"class": "form-control", "autocomplete": "new-password"},
                render_value=False,  # never pre-fill the token in the edit form
            ),
            "webhook_verify_token": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        help_texts = {
            "api_token": "Permanent token from the Meta developer console. "
                         "Leave blank to keep the existing token when editing.",
            "webhook_verify_token": "Any secret string you set in the Meta webhook config.",
        }

    def __init__(self, *args, **kwargs):
        self._is_edit = kwargs.pop("is_edit", False)
        super().__init__(*args, **kwargs)
        if self._is_edit:
            self.fields["api_token"].required = False
            self.fields["api_token"].help_text = (
                "Leave blank to keep the current token. Enter a new value to replace it."
            )

    def save(self, commit=True):
        instance = super().save(commit=False)
        # If the token field is blank on edit, keep the existing encrypted value
        if self._is_edit and not self.cleaned_data.get("api_token"):
            instance.api_token = WhatsAppAccount.objects.get(pk=instance.pk).api_token
        if commit:
            instance.save()
        return instance


class AccountAssignmentForm(forms.ModelForm):
    class Meta:
        model = AccountAssignment
        fields = ["user", "account"]
        widgets = {
            "user": forms.Select(attrs={"class": "form-select"}),
            "account": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(role="agent", is_active=True)
        self.fields["account"].queryset = WhatsAppAccount.objects.filter(is_active=True)
