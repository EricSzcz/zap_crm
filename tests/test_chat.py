"""
Unit tests for the `chat` app (Module 5).

Coverage:
- ConversationListView: agent sees own convs, anonymous redirect, cross-agent isolation
- ConversationDetailView: agent can view own conv, 404 for others, anonymous redirect
- SendMessageView: valid send (mocked API success + failure), empty text, HTMX partial,
  cross-agent 404
- ChatConsumer: connect/disconnect, auth guard, access guard, chat.message event forwarding
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.urls import reverse

from conversations.models import Conversation, Message

from .conftest import (
    AccountAssignmentFactory,
    AgentUserFactory,
    ContactFactory,
    ConversationFactory,
    MessageFactory,
    WhatsAppAccountFactory,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LIST_URL = "/chat/"


def detail_url(pk):
    return f"/chat/{pk}/"


def send_url(pk):
    return f"/chat/{pk}/send/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_meta_success(wamid="wamid.OUT001"):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"messages": [{"id": wamid}]}
    return mock_resp


def _mock_meta_failure():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("API error")
    return mock_resp


# ===========================================================================
# ConversationListView
# ===========================================================================

@pytest.mark.django_db
class TestConversationListView:
    def test_anonymous_redirected_to_login(self, client):
        response = client.get(LIST_URL)
        assert response.status_code == 302
        assert "/login" in response["Location"]

    def test_agent_sees_assigned_conversations(self, agent_client, agent_user):
        account = WhatsAppAccountFactory()
        account.save()
        AccountAssignmentFactory(user=agent_user, account=account)
        conv = ConversationFactory(whatsapp_account=account)

        response = agent_client.get(LIST_URL)
        assert response.status_code == 200
        assert conv in response.context["conversations"]

    def test_agent_does_not_see_others_conversations(self, agent_client):
        # A conversation on an account NOT assigned to the logged-in agent
        other_account = WhatsAppAccountFactory()
        other_account.save()
        ConversationFactory(whatsapp_account=other_account)

        response = agent_client.get(LIST_URL)
        assert response.status_code == 200
        assert list(response.context["conversations"]) == []

    def test_empty_list_renders_ok(self, agent_client):
        response = agent_client.get(LIST_URL)
        assert response.status_code == 200
        assert list(response.context["conversations"]) == []

    def test_admin_can_also_access_chat_list(self, admin_client):
        response = admin_client.get(LIST_URL)
        assert response.status_code == 200

    def test_multiple_conversations_shown(self, agent_client, agent_user):
        account = WhatsAppAccountFactory()
        account.save()
        AccountAssignmentFactory(user=agent_user, account=account)
        c1 = ConversationFactory(whatsapp_account=account)
        c2 = ConversationFactory(whatsapp_account=account)

        response = agent_client.get(LIST_URL)
        conv_ids = {c.pk for c in response.context["conversations"]}
        assert {c1.pk, c2.pk} == conv_ids


# ===========================================================================
# ConversationDetailView
# ===========================================================================

@pytest.mark.django_db
class TestConversationDetailView:
    def _setup(self, agent_user):
        account = WhatsAppAccountFactory()
        account.save()
        AccountAssignmentFactory(user=agent_user, account=account)
        conv = ConversationFactory(whatsapp_account=account)
        return conv

    def test_anonymous_redirected_to_login(self, client):
        conv = ConversationFactory()
        response = client.get(detail_url(conv.pk))
        assert response.status_code == 302
        assert "/login" in response["Location"]

    def test_agent_can_view_own_conversation(self, agent_client, agent_user):
        conv = self._setup(agent_user)
        response = agent_client.get(detail_url(conv.pk))
        assert response.status_code == 200
        assert response.context["conversation"].pk == conv.pk

    def test_agent_gets_404_for_others_conversation(self, agent_client):
        other_account = WhatsAppAccountFactory()
        other_account.save()
        other_conv = ConversationFactory(whatsapp_account=other_account)
        response = agent_client.get(detail_url(other_conv.pk))
        assert response.status_code == 404

    def test_messages_in_context(self, agent_client, agent_user):
        conv = self._setup(agent_user)
        msg = MessageFactory(conversation=conv)
        response = agent_client.get(detail_url(conv.pk))
        msg_ids = [m.pk for m in response.context["messages"]]
        assert msg.pk in msg_ids

    def test_conversations_sidebar_in_context(self, agent_client, agent_user):
        conv = self._setup(agent_user)
        response = agent_client.get(detail_url(conv.pk))
        assert "conversations" in response.context

    def test_nonexistent_conversation_returns_404(self, agent_client):
        response = agent_client.get(detail_url(999999))
        assert response.status_code == 404


# ===========================================================================
# SendMessageView
# ===========================================================================

@pytest.mark.django_db
class TestSendMessageView:
    def _setup(self, agent_user):
        account = WhatsAppAccountFactory()
        account.save()
        AccountAssignmentFactory(user=agent_user, account=account)
        conv = ConversationFactory(whatsapp_account=account)
        return conv

    def test_empty_text_returns_204(self, agent_client, agent_user):
        conv = self._setup(agent_user)
        response = agent_client.post(send_url(conv.pk), {"text": "   "})
        assert response.status_code == 204
        assert not Message.objects.filter(conversation=conv).exists()

    def test_anonymous_redirected_to_login(self, client):
        conv = ConversationFactory()
        response = client.post(send_url(conv.pk), {"text": "hi"})
        assert response.status_code == 302

    def test_agent_cannot_send_to_others_conversation(self, agent_client):
        other_account = WhatsAppAccountFactory()
        other_account.save()
        other_conv = ConversationFactory(whatsapp_account=other_account)
        with patch("chat.views.requests.post") as mock_post:
            mock_post.return_value = _mock_meta_success()
            response = agent_client.post(send_url(other_conv.pk), {"text": "hi"})
        assert response.status_code == 404

    @patch("chat.views.requests.post")
    def test_successful_send_creates_message(self, mock_post, agent_client, agent_user):
        mock_post.return_value = _mock_meta_success("wamid.SEND01")
        conv = self._setup(agent_user)
        agent_client.post(send_url(conv.pk), {"text": "Hello"})
        msg = Message.objects.get(conversation=conv)
        assert msg.content == "Hello"
        assert msg.direction == "outbound"

    @patch("chat.views.requests.post")
    def test_successful_send_marks_message_sent(self, mock_post, agent_client, agent_user):
        mock_post.return_value = _mock_meta_success("wamid.SEND02")
        conv = self._setup(agent_user)
        agent_client.post(send_url(conv.pk), {"text": "Hi"})
        msg = Message.objects.get(conversation=conv)
        assert msg.status == "sent"
        assert msg.whatsapp_message_id == "wamid.SEND02"

    @patch("chat.views.requests.post")
    def test_api_failure_marks_message_failed(self, mock_post, agent_client, agent_user):
        mock_post.return_value = _mock_meta_failure()
        conv = self._setup(agent_user)
        response = agent_client.post(send_url(conv.pk), {"text": "Hi"})
        msg = Message.objects.get(conversation=conv)
        assert msg.status == "failed"
        assert response.status_code == 200  # HTMX still gets the partial

    @patch("chat.views.requests.post")
    def test_send_returns_partial_html(self, mock_post, agent_client, agent_user):
        mock_post.return_value = _mock_meta_success()
        conv = self._setup(agent_user)
        response = agent_client.post(send_url(conv.pk), {"text": "Hi"})
        assert response.status_code == 200
        # The partial contains the message text
        assert b"Hi" in response.content

    @patch("chat.views.requests.post")
    def test_send_touches_conversation(self, mock_post, agent_client, agent_user):
        mock_post.return_value = _mock_meta_success()
        conv = self._setup(agent_user)
        agent_client.post(send_url(conv.pk), {"text": "touch test"})
        conv.refresh_from_db()
        assert conv.last_message_at is not None

    @patch("chat.views.requests.post")
    def test_meta_api_called_with_correct_payload(self, mock_post, agent_client, agent_user):
        mock_post.return_value = _mock_meta_success()
        conv = self._setup(agent_user)
        agent_client.post(send_url(conv.pk), {"text": "payload check"})
        call_kwargs = mock_post.call_args
        sent_json = call_kwargs[1]["json"]
        assert sent_json["type"] == "text"
        assert sent_json["text"]["body"] == "payload check"
        assert sent_json["to"] == conv.contact.phone_number

    @patch("chat.views.requests.post")
    def test_send_to_closed_conversation_creates_message(self, mock_post, agent_client, agent_user):
        """Sending to a closed conv is not blocked at the view level (business logic allows it)."""
        mock_post.return_value = _mock_meta_success()
        conv = self._setup(agent_user)
        conv.close()
        response = agent_client.post(send_url(conv.pk), {"text": "reopen test"})
        assert response.status_code == 200


# ===========================================================================
# ChatConsumer (WebSocket)
# ===========================================================================

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestChatConsumer:
    """
    WebSocket consumer tests using channels.testing.WebsocketCommunicator.
    transaction=True is required because consumer DB calls run in a thread pool.

    We wrap only the URLRouter (not AuthMiddlewareStack) so we can inject the
    user directly into scope without the auth middleware overwriting it.
    """

    async def _make_setup(self):
        """Create an agent with an assigned account and a conversation."""
        from asgiref.sync import sync_to_async

        @sync_to_async
        def _create():
            agent = AgentUserFactory()
            account = WhatsAppAccountFactory()
            account.save()
            AccountAssignmentFactory(user=agent, account=account)
            conv = ConversationFactory(whatsapp_account=account)
            return agent, conv

        return await _create()

    def _make_communicator(self, user, conv_pk):
        from channels.routing import URLRouter
        from django.urls import re_path
        from chat.consumer import ChatConsumer

        app = URLRouter([
            re_path(r"^ws/chat/(?P<pk>\d+)/$", ChatConsumer.as_asgi()),
        ])
        communicator = WebsocketCommunicator(app, f"/ws/chat/{conv_pk}/")
        communicator.scope["user"] = user
        return communicator

    async def test_authenticated_agent_connects(self):
        agent, conv = await self._make_setup()
        communicator = self._make_communicator(agent, conv.pk)
        connected, _ = await communicator.connect()
        assert connected
        await communicator.disconnect()

    async def test_unauthenticated_connection_rejected(self):
        from django.contrib.auth.models import AnonymousUser

        _, conv = await self._make_setup()
        communicator = self._make_communicator(AnonymousUser(), conv.pk)
        connected, code = await communicator.connect()
        assert not connected

    async def test_agent_without_access_rejected(self):
        from asgiref.sync import sync_to_async

        @sync_to_async
        def _create_other_conv():
            other_account = WhatsAppAccountFactory()
            other_account.save()
            return ConversationFactory(whatsapp_account=other_account)

        agent, _ = await self._make_setup()
        other_conv = await _create_other_conv()
        communicator = self._make_communicator(agent, other_conv.pk)
        connected, _ = await communicator.connect()
        assert not connected

    async def test_chat_message_event_forwarded(self):
        agent, conv = await self._make_setup()
        communicator = self._make_communicator(agent, conv.pk)
        connected, _ = await communicator.connect()
        assert connected

        # Simulate the channel layer pushing a chat.message event
        channel_layer = get_channel_layer()
        group_name = f"chat_{conv.pk}"
        await channel_layer.group_send(group_name, {
            "type": "chat.message",
            "message_id": 42,
            "conversation_id": conv.pk,
            "content": "Hello from WS",
            "direction": "inbound",
            "status": "delivered",
            "created_at": "2024-01-01T12:00:00",
            "contact_name": "Tester",
        })

        response = await communicator.receive_json_from()
        assert response["type"] == "chat.message"
        assert response["content"] == "Hello from WS"
        assert response["direction"] == "inbound"
        assert response["contact_name"] == "Tester"

        await communicator.disconnect()

    async def test_disconnect_leaves_group(self):
        """After disconnect, messages to the group are not delivered."""
        agent, conv = await self._make_setup()
        communicator = self._make_communicator(agent, conv.pk)
        connected, _ = await communicator.connect()
        assert connected
        await communicator.disconnect()

        # Push to the group after disconnect — should NOT be receivable
        channel_layer = get_channel_layer()
        await channel_layer.group_send(f"chat_{conv.pk}", {
            "type": "chat.message",
            "message_id": 1,
            "conversation_id": conv.pk,
            "content": "after disconnect",
            "direction": "inbound",
            "status": "delivered",
            "created_at": "2024-01-01T12:00:00",
            "contact_name": "",
        })
        assert await communicator.receive_nothing()
