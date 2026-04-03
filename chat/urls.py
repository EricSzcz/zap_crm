from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.ConversationListView.as_view(), name="list"),
    path("<int:pk>/", views.ConversationDetailView.as_view(), name="detail"),
    path("<int:pk>/send/", views.SendMessageView.as_view(), name="send"),
]
