from django.urls import path
from .views import WhatsAppWebhookView

app_name = "webhooks"

urlpatterns = [
    path("whatsapp/", WhatsAppWebhookView.as_view(), name="whatsapp"),
]
