from django.contrib import admin
from .models import Contact, Conversation, Message


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ["phone_number", "name", "created_at"]
    search_fields = ["phone_number", "name"]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["pk", "contact", "whatsapp_account", "assigned_agent", "status", "last_message_at"]
    list_filter = ["status", "whatsapp_account"]
    raw_id_fields = ["contact", "assigned_agent"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["pk", "conversation", "direction", "status", "created_at"]
    list_filter = ["direction", "status"]
    raw_id_fields = ["conversation"]
