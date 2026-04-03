"""
webhooks/tasks.py

Celery tasks for webhook processing.
"""
import logging

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def process_inbound_message(self, message_id: int):
    """
    Push a newly-received inbound message to the correct WebSocket channel group.

    Channel group name: chat_<conversation_id>
    Consumers subscribed to that group (agents with the chat open) will
    receive the event and append the message to their UI without a page reload.

    Retries up to 3 times on transient errors (e.g. Redis blip).
    """
    from conversations.models import Message  # local import avoids circular imports

    try:
        message = Message.objects.select_related(
            "conversation__contact",
            "conversation__whatsapp_account",
        ).get(pk=message_id)
    except Message.DoesNotExist:
        logger.error("process_inbound_message: Message pk=%s not found", message_id)
        return

    conversation = message.conversation
    group_name = f"chat_{conversation.pk}"

    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.error("No channel layer configured — cannot push message pk=%s", message_id)
        return

    payload = {
        "type": "chat.message",          # maps to consumer method chat_message()
        "message_id": message.pk,
        "conversation_id": conversation.pk,
        "content": message.content,
        "direction": message.direction,
        "status": message.status,
        "created_at": message.created_at.isoformat(),
        "contact_name": conversation.contact.display_name,
    }

    try:
        async_to_sync(channel_layer.group_send)(group_name, payload)
        logger.debug(
            "Pushed message pk=%s to channel group %s", message_id, group_name
        )
    except Exception as exc:
        logger.warning("Failed to push to channel group %s: %s", group_name, exc)
        raise self.retry(exc=exc)
