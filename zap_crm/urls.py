from django.urls import path, include

urlpatterns = [
    path("", include("accounts.urls")),
    path("admin-panel/", include("whatsapp_config.urls")),
    path("chat/", include("chat.urls")),
    path("webhooks/", include("webhooks.urls")),
]
