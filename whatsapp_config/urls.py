from django.urls import path
from . import views

app_name = "admin_panel"

urlpatterns = [
    # Dashboard
    path("", views.DashboardView.as_view(), name="dashboard"),

    # WhatsApp accounts
    path("whatsapp-accounts/", views.AccountListView.as_view(), name="account_list"),
    path("whatsapp-accounts/create/", views.AccountCreateView.as_view(), name="account_create"),
    path("whatsapp-accounts/<int:pk>/edit/", views.AccountEditView.as_view(), name="account_edit"),
    path("whatsapp-accounts/<int:pk>/delete/", views.AccountDeleteView.as_view(), name="account_delete"),
    path("whatsapp-accounts/<int:pk>/toggle/", views.AccountToggleActiveView.as_view(), name="account_toggle"),
    path("whatsapp-accounts/<int:pk>/test/", views.AccountTestConnectionView.as_view(), name="account_test"),

    # Users / agents
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/create/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/toggle/", views.UserToggleActiveView.as_view(), name="user_toggle"),

    # Assignments
    path("assignments/", views.AssignmentListView.as_view(), name="assignment_list"),
    path("assignments/<int:pk>/delete/", views.AssignmentDeleteView.as_view(), name="assignment_delete"),

    # Conversation monitor
    path("conversations/", views.ConversationMonitorView.as_view(), name="conversation_monitor"),
]
