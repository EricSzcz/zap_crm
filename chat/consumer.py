"""
chat/consumer.py

WebSocket consumer for the agent chat UI.

Channel group: chat_<conversation_id>

- On connect  : authenticate, verify access, join group.
- On disconnect: leave group.
- On chat.message from group: forward payload as JSON to the browser client.

Agents do not send messages via WebSocket; outbound messages go through the
HTTP SendMessageView so they can call the Meta API synchronously and return
an HTMX partial.  Inbound messages arrive via the webhook → Celery task →
channel layer pipeline.
"""
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4001)
            return

        self.conversation_id = self.scope["url_route"]["kwargs"]["pk"]

        can_access = await self._can_access(user, self.conversation_id)
        if not can_access:
            await self.close(code=4003)
            return

        self.group_name = f"chat_{self.conversation_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.debug("WS connected: user=%s conv=%s", user.pk, self.conversation_id)

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            logger.debug("WS disconnected: group=%s code=%s", self.group_name, close_code)

    # receive() not implemented — clients only listen, never push via WS.

    # ------------------------------------------------------------------
    # Group event handlers
    # ------------------------------------------------------------------

    async def chat_message(self, event):
        """Receive a chat.message event from the channel group and forward it."""
        await self.send(text_data=json.dumps({
            "type": "chat.message",
            "message_id": event["message_id"],
            "conversation_id": event["conversation_id"],
            "content": event["content"],
            "direction": event["direction"],
            "status": event["status"],
            "created_at": event["created_at"],
            "contact_name": event["contact_name"],
        }))

    # ------------------------------------------------------------------
    # DB helper
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _can_access(self, user, conversation_id):
        from conversations.models import Conversation
        return Conversation.objects.for_agent(user).filter(pk=conversation_id).exists()
