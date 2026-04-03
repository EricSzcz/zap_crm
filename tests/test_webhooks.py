"""
Unit tests for the `webhooks` app (Module 4).

Coverage:
- GET verification challenge: correct token, wrong token, missing params
- POST HMAC validation: valid signature, invalid signature, missing header
- Payload parsing: text message, non-text types, non-message fields
- Database writes: Contact created, Conversation created, Message created
- Deduplication: same wamid is silently skipped
- Contact name update: name enriched on repeat contact
- Reuse open conversation: second message reuses existing open conversation
- Creates new conversation when all existing are closed
- Unknown phone_number_id: silently ignored
- Celery task: process_inbound_message sends to channel layer group
- Celery task: missing message_id is handled gracefully
"""
import hashlib
import hmac
import json

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.test import override_settings
from django.urls import reverse

from conversations.models import Contact, Conversation, Message
from whatsapp_config.models import AccountAssignment, WhatsAppAccount

from .conftest import AgentUserFactory, WhatsAppAccountFactory

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

WEBHOOK_URL = "/webhooks/whatsapp/"
APP_SECRET = "test-webhook-secret"  # must match test_settings.META_WEBHOOK_APP_SECRET


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    """Return 'sha256=<hex>' for the given body bytes."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post(client, payload: dict, secret: str = APP_SECRET, extra_headers: dict | None = None):
    """Sign and POST a JSON payload to the webhook endpoint."""
    body = json.dumps(payload).encode()
    sig = _sign(body, secret)
    headers = {"HTTP_X_HUB_SIGNATURE_256": sig, **(extra_headers or {})}
    return client.post(
        WEBHOOK_URL,
        data=body,
        content_type="application/json",
        **headers,
    )


def _meta_payload(phone_number_id: str, from_number: str, wamid: str, text: str, name: str = "") -> dict:
    """Build a minimal Meta webhook payload for a single inbound text message."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BUSINESS_ACCOUNT_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551234567",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [
                                {"profile": {"name": name}, "wa_id": from_number}
                            ],
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": wamid,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


# ===========================================================================
# GET — verification challenge
# ===========================================================================

@pytest.mark.django_db
class TestWebhookVerification:
    def _make_account(self, verify_token="my-verify-token"):
        account = WhatsAppAccountFactory(webhook_verify_token=verify_token)
        account.save()
        return account

    def test_correct_token_returns_challenge(self, client):
        self._make_account("abc123")
        response = client.get(
            WEBHOOK_URL,
            {"hub.mode": "subscribe", "hub.verify_token": "abc123", "hub.challenge": "CHALLENGE_STRING"},
        )
        assert response.status_code == 200
        assert response.content == b"CHALLENGE_STRING"

    def test_wrong_token_returns_403(self, client):
        self._make_account("correct-token")
        response = client.get(
            WEBHOOK_URL,
            {"hub.mode": "subscribe", "hub.verify_token": "wrong-token", "hub.challenge": "CHAL"},
        )
        assert response.status_code == 403

    def test_missing_token_returns_400(self, client):
        response = client.get(WEBHOOK_URL, {"hub.mode": "subscribe", "hub.challenge": "CHAL"})
        assert response.status_code == 400

    def test_wrong_mode_returns_400(self, client):
        self._make_account("tok")
        response = client.get(
            WEBHOOK_URL,
            {"hub.mode": "unsubscribe", "hub.verify_token": "tok", "hub.challenge": "CHAL"},
        )
        assert response.status_code == 400

    def test_inactive_account_token_rejected(self, client):
        """An inactive account's verify_token must not be accepted."""
        account = WhatsAppAccountFactory(webhook_verify_token="inactive-tok", is_active=False)
        account.save()
        response = client.get(
            WEBHOOK_URL,
            {"hub.mode": "subscribe", "hub.verify_token": "inactive-tok", "hub.challenge": "X"},
        )
        assert response.status_code == 403

    def test_challenge_returned_verbatim(self, client):
        """The exact challenge string (including special chars) must be echoed back."""
        self._make_account("tok2")
        challenge = "abc123!@#"
        response = client.get(
            WEBHOOK_URL,
            {"hub.mode": "subscribe", "hub.verify_token": "tok2", "hub.challenge": challenge},
        )
        assert response.status_code == 200
        assert response.content.decode() == challenge


# ===========================================================================
# POST — HMAC validation
# ===========================================================================

@pytest.mark.django_db
class TestWebhookHmacValidation:
    def test_valid_signature_returns_200(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(account.phone_number_id, "+15550001111", "wamid.HMAC1", "Hi")
        response = _post(client, payload)
        assert response.status_code == 200

    def test_invalid_signature_returns_403(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(account.phone_number_id, "+15550001112", "wamid.HMAC2", "Hi")
        body = json.dumps(payload).encode()
        response = client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=badhash",
        )
        assert response.status_code == 403

    def test_missing_signature_header_returns_403(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(account.phone_number_id, "+15550001113", "wamid.HMAC3", "Hi")
        response = client.post(
            WEBHOOK_URL,
            data=json.dumps(payload).encode(),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_signature_with_wrong_secret_returns_403(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(account.phone_number_id, "+15550001114", "wamid.HMAC4", "Hi")
        response = _post(client, payload, secret="wrong-secret")
        assert response.status_code == 403

    @override_settings(META_WEBHOOK_APP_SECRET="")
    def test_unconfigured_secret_returns_403(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(account.phone_number_id, "+15550001115", "wamid.HMAC5", "Hi")
        response = _post(client, payload, secret="")
        assert response.status_code == 403

    def test_non_json_body_returns_400(self, client):
        body = b"not-json"
        sig = _sign(body)
        response = client.post(
            WEBHOOK_URL,
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=sig,
        )
        assert response.status_code == 400

    def test_wrong_object_type_returns_200_ignored(self, client):
        """Payloads for non-WhatsApp objects should be acknowledged without processing."""
        payload = {"object": "instagram", "entry": []}
        response = _post(client, payload)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "ignored"


# ===========================================================================
# POST — database writes
# ===========================================================================

@pytest.mark.django_db
class TestWebhookDatabaseWrites:
    def test_creates_contact_on_first_message(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550002001", "wamid.DB001", "Hello", name="Alice"
        )
        _post(client, payload)
        assert Contact.objects.filter(phone_number="+15550002001").exists()

    def test_contact_name_saved(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550002002", "wamid.DB002", "Hello", name="Bob"
        )
        _post(client, payload)
        contact = Contact.objects.get(phone_number="+15550002002")
        assert contact.name == "Bob"

    def test_creates_conversation(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550002003", "wamid.DB003", "Hello"
        )
        _post(client, payload)
        assert Conversation.objects.filter(
            contact__phone_number="+15550002003",
            whatsapp_account=account,
        ).exists()

    def test_conversation_status_is_open(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550002004", "wamid.DB004", "Hello"
        )
        _post(client, payload)
        conv = Conversation.objects.get(contact__phone_number="+15550002004")
        assert conv.status == "open"

    def test_creates_inbound_message(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550002005", "wamid.DB005", "Test message"
        )
        _post(client, payload)
        msg = Message.objects.get(whatsapp_message_id="wamid.DB005")
        assert msg.direction == "inbound"
        assert msg.content == "Test message"
        assert msg.status == "delivered"

    def test_message_wamid_is_stored(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550002006", "wamid.DB006", "Hi"
        )
        _post(client, payload)
        assert Message.objects.filter(whatsapp_message_id="wamid.DB006").exists()

    def test_conversation_last_message_at_updated(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550002007", "wamid.DB007", "Hi"
        )
        _post(client, payload)
        conv = Conversation.objects.get(contact__phone_number="+15550002007")
        assert conv.last_message_at is not None

    def test_unknown_phone_number_id_silently_ignored(self, client):
        """Webhooks for accounts we don't know about should not error."""
        payload = _meta_payload("unknown-pid", "+15550002008", "wamid.DB008", "Hi")
        response = _post(client, payload)
        assert response.status_code == 200
        assert not Message.objects.filter(whatsapp_message_id="wamid.DB008").exists()

    def test_non_text_message_type_skipped(self, client):
        """Image / audio / video messages are skipped without error."""
        account = WhatsAppAccountFactory()
        account.save()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "BIZ",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": account.phone_number_id},
                                "contacts": [],
                                "messages": [
                                    {
                                        "from": "+15550002009",
                                        "id": "wamid.IMG001",
                                        "type": "image",
                                        "image": {"id": "img123"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        response = _post(client, payload)
        assert response.status_code == 200
        assert not Message.objects.filter(whatsapp_message_id="wamid.IMG001").exists()

    def test_non_message_field_skipped(self, client):
        """Status update changes (field != 'messages') are silently skipped."""
        account = WhatsAppAccountFactory()
        account.save()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "BIZ",
                    "changes": [
                        {
                            "field": "message_template_status_update",
                            "value": {},
                        }
                    ],
                }
            ],
        }
        response = _post(client, payload)
        assert response.status_code == 200


# ===========================================================================
# Deduplication
# ===========================================================================

@pytest.mark.django_db
class TestWebhookDeduplication:
    def test_duplicate_wamid_not_stored_twice(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550003001", "wamid.DUPE01", "Hello"
        )
        _post(client, payload)
        _post(client, payload)  # second delivery
        assert Message.objects.filter(whatsapp_message_id="wamid.DUPE01").count() == 1

    def test_duplicate_does_not_create_extra_conversation(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550003002", "wamid.DUPE02", "Hello"
        )
        _post(client, payload)
        _post(client, payload)
        assert Conversation.objects.filter(contact__phone_number="+15550003002").count() == 1

    def test_duplicate_still_returns_200(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        payload = _meta_payload(
            account.phone_number_id, "+15550003003", "wamid.DUPE03", "Hello"
        )
        _post(client, payload)
        response = _post(client, payload)
        assert response.status_code == 200


# ===========================================================================
# Conversation reuse / creation logic
# ===========================================================================

@pytest.mark.django_db
class TestConversationReuseLogic:
    def test_second_message_from_same_contact_reuses_conversation(self, client):
        account = WhatsAppAccountFactory()
        account.save()
        _post(client, _meta_payload(account.phone_number_id, "+15550004001", "wamid.REUSE01", "Hi"))
        _post(client, _meta_payload(account.phone_number_id, "+15550004001", "wamid.REUSE02", "Again"))
        assert Conversation.objects.filter(contact__phone_number="+15550004001").count() == 1
        assert Message.objects.filter(conversation__contact__phone_number="+15550004001").count() == 2

    def test_new_conversation_created_when_all_closed(self, client):
        from conversations.models import Conversation as Conv
        account = WhatsAppAccountFactory()
        account.save()
        _post(client, _meta_payload(account.phone_number_id, "+15550004002", "wamid.NEWCONV01", "Hi"))
        Conv.objects.filter(contact__phone_number="+15550004002").update(status="closed")
        _post(client, _meta_payload(account.phone_number_id, "+15550004002", "wamid.NEWCONV02", "Back again"))
        assert Conv.objects.filter(contact__phone_number="+15550004002").count() == 2

    def test_contact_name_enriched_on_second_message(self, client):
        """If contact was created without a name, it is updated when name arrives."""
        account = WhatsAppAccountFactory()
        account.save()
        # First message with no name
        _post(client, _meta_payload(account.phone_number_id, "+15550004003", "wamid.NAME01", "Hi", name=""))
        contact = Contact.objects.get(phone_number="+15550004003")
        assert contact.name == ""
        # Second message with a name
        _post(client, _meta_payload(account.phone_number_id, "+15550004003", "wamid.NAME02", "Again", name="Carol"))
        contact.refresh_from_db()
        assert contact.name == "Carol"

    def test_existing_contact_name_not_overwritten(self, client):
        """If contact already has a name, it should not be overwritten by subsequent messages."""
        account = WhatsAppAccountFactory()
        account.save()
        _post(client, _meta_payload(account.phone_number_id, "+15550004004", "wamid.NAME03", "Hi", name="Dave"))
        _post(client, _meta_payload(account.phone_number_id, "+15550004004", "wamid.NAME04", "Hi", name="Different"))
        contact = Contact.objects.get(phone_number="+15550004004")
        assert contact.name == "Dave"


# ===========================================================================
# Celery task: process_inbound_message
# ===========================================================================

@pytest.mark.django_db
class TestProcessInboundMessageTask:
    def _create_message(self):
        account = WhatsAppAccountFactory()
        account.save()
        contact = Contact.objects.create(phone_number="+15550005001")
        conv = Conversation.objects.create(contact=contact, whatsapp_account=account)
        msg = Message.objects.create(
            conversation=conv,
            content="task test",
            direction="inbound",
            whatsapp_message_id="wamid.TASK01",
            status="delivered",
        )
        return msg, conv

    def _subscribe_to_group(self, group_name):
        """
        Subscribe a fresh channel to ``group_name`` and return the channel name.

        InMemoryChannelLayer.group_send delivers to channels that are subscribed
        to the group, not to a channel whose name equals the group name.
        """
        channel_layer = get_channel_layer()
        channel_name = async_to_sync(channel_layer.new_channel)()
        async_to_sync(channel_layer.group_add)(group_name, channel_name)
        return channel_name

    def test_task_sends_to_channel_group(self):
        """process_inbound_message pushes to the correct channel group."""
        from webhooks.tasks import process_inbound_message

        msg, conv = self._create_message()
        channel_layer = get_channel_layer()
        group_name = f"chat_{conv.pk}"

        # Subscribe a test channel to the group BEFORE running the task
        channel_name = self._subscribe_to_group(group_name)

        # Run the task synchronously (CELERY_TASK_ALWAYS_EAGER = True in test settings)
        process_inbound_message(msg.pk)

        # Read the message from the subscribed channel
        received = async_to_sync(channel_layer.receive)(channel_name)
        assert received["type"] == "chat.message"
        assert received["message_id"] == msg.pk
        assert received["conversation_id"] == conv.pk
        assert received["content"] == "task test"
        assert received["direction"] == "inbound"

    def test_task_includes_contact_display_name(self):
        from webhooks.tasks import process_inbound_message

        account = WhatsAppAccountFactory()
        account.save()
        contact = Contact.objects.create(phone_number="+15550005002", name="Eve")
        conv = Conversation.objects.create(contact=contact, whatsapp_account=account)
        msg = Message.objects.create(
            conversation=conv,
            content="hi",
            direction="inbound",
            whatsapp_message_id="wamid.TASK02",
            status="delivered",
        )
        channel_layer = get_channel_layer()
        group_name = f"chat_{conv.pk}"
        channel_name = self._subscribe_to_group(group_name)

        process_inbound_message(msg.pk)

        received = async_to_sync(channel_layer.receive)(channel_name)
        assert received["contact_name"] == "Eve"

    def test_task_missing_message_id_does_not_crash(self):
        """Gracefully handle a message_id that no longer exists."""
        from webhooks.tasks import process_inbound_message
        # Should not raise
        process_inbound_message(999999)

    def test_task_includes_created_at_iso_string(self):
        from webhooks.tasks import process_inbound_message

        account = WhatsAppAccountFactory()
        account.save()
        contact = Contact.objects.create(phone_number="+15550005003")
        conv = Conversation.objects.create(contact=contact, whatsapp_account=account)
        msg = Message.objects.create(
            conversation=conv,
            content="ts test",
            direction="inbound",
            whatsapp_message_id="wamid.TASK03",
            status="delivered",
        )
        channel_layer = get_channel_layer()
        group_name = f"chat_{conv.pk}"
        channel_name = self._subscribe_to_group(group_name)

        process_inbound_message(msg.pk)

        received = async_to_sync(channel_layer.receive)(channel_name)
        # created_at must be a valid ISO-8601 string
        assert "T" in received["created_at"]


# ===========================================================================
# _parse_inbound_messages helper (unit-tested directly)
# ===========================================================================

class TestParseInboundMessages:
    """White-box tests for the payload parser — no DB needed."""

    def _run(self, payload):
        from webhooks.views import _parse_inbound_messages
        return list(_parse_inbound_messages(payload))

    def test_parses_single_text_message(self):
        payload = _meta_payload("pid1", "+111", "wamid.P01", "hello", "Alice")
        results = self._run(payload)
        assert len(results) == 1
        phone_number_id, from_num, name, wamid, text = results[0]
        assert phone_number_id == "pid1"
        assert from_num == "+111"
        assert name == "Alice"
        assert wamid == "wamid.P01"
        assert text == "hello"

    def test_skips_non_text_message_types(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "x", "changes": [{"field": "messages", "value": {
                "metadata": {"phone_number_id": "pid2"},
                "contacts": [],
                "messages": [{"from": "+222", "id": "wamid.P02", "type": "image", "image": {}}],
            }}]}],
        }
        assert self._run(payload) == []

    def test_skips_non_messages_field(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "x", "changes": [{"field": "statuses", "value": {}}]}],
        }
        assert self._run(payload) == []

    def test_empty_entry_list(self):
        assert self._run({"object": "whatsapp_business_account", "entry": []}) == []

    def test_multiple_messages_in_one_payload(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "x", "changes": [{"field": "messages", "value": {
                "metadata": {"phone_number_id": "pid3"},
                "contacts": [
                    {"profile": {"name": "A"}, "wa_id": "+333"},
                    {"profile": {"name": "B"}, "wa_id": "+444"},
                ],
                "messages": [
                    {"from": "+333", "id": "wamid.M01", "type": "text", "text": {"body": "msg1"}},
                    {"from": "+444", "id": "wamid.M02", "type": "text", "text": {"body": "msg2"}},
                ],
            }}]}],
        }
        results = self._run(payload)
        assert len(results) == 2
        wamids = {r[3] for r in results}
        assert wamids == {"wamid.M01", "wamid.M02"}

    def test_contact_name_empty_when_not_in_contacts_array(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "x", "changes": [{"field": "messages", "value": {
                "metadata": {"phone_number_id": "pid4"},
                "contacts": [],  # no contact profile
                "messages": [{"from": "+555", "id": "wamid.P03", "type": "text", "text": {"body": "hi"}}],
            }}]}],
        }
        results = self._run(payload)
        assert results[0][2] == ""  # name is empty string
