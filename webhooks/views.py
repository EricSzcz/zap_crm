"""
webhooks/views.py

Single endpoint: /webhooks/whatsapp/

GET  — Meta webhook verification challenge
POST — Inbound message processing

Security
--------
- GET: verify_token must match a known WhatsAppAccount.webhook_verify_token
- POST: X-Hub-Signature-256 header is validated with HMAC-SHA256
         using settings.META_WEBHOOK_APP_SECRET before any payload is parsed.

The endpoint is CSRF-exempt (Meta sends no CSRF cookie).
"""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from conversations.models import Contact, Conversation, Message
from whatsapp_config.models import WhatsAppAccount

from .tasks import process_inbound_message

logger = logging.getLogger(__name__)


def _verify_hmac(request) -> bool:
    """
    Validate the X-Hub-Signature-256 header.

    Meta computes: HMAC-SHA256(app_secret, raw_request_body)
    and sends it as 'sha256=<hex_digest>'.
    """
    secret = settings.META_WEBHOOK_APP_SECRET
    if not secret:
        logger.warning("META_WEBHOOK_APP_SECRET is not configured — rejecting all POSTs")
        return False

    header = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
    if not header.startswith("sha256="):
        return False

    received_hex = header[len("sha256="):]
    computed = hmac.new(
        secret.encode(),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, received_hex)


def _parse_inbound_messages(payload: dict):
    """
    Yield (phone_number_id, from_number, sender_name, wamid, text_body) tuples
    for every text message in a Meta webhook payload.

    Meta payload shape:
    {
      "entry": [{
        "changes": [{
          "value": {
            "metadata": {"phone_number_id": "..."},
            "contacts": [{"profile": {"name": "..."}, "wa_id": "..."}],
            "messages": [{"from": "...", "id": "wamid...", "type": "text",
                          "text": {"body": "..."}}]
          }
        }]
      }]
    }
    """
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if change.get("field") != "messages":
                continue

            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

            # Build a quick lookup of name by wa_id from the contacts array
            contact_names = {
                c["wa_id"]: c.get("profile", {}).get("name", "")
                for c in value.get("contacts", [])
            }

            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue  # non-text (image, audio, etc.) ignored for now
                yield (
                    phone_number_id,
                    msg["from"],
                    contact_names.get(msg["from"], ""),
                    msg["id"],          # wamid
                    msg["text"]["body"],
                )


@method_decorator(csrf_exempt, name="dispatch")
class WhatsAppWebhookView(View):
    """
    GET  /webhooks/whatsapp/?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…
    POST /webhooks/whatsapp/
    """

    # ------------------------------------------------------------------
    # GET — verification challenge
    # ------------------------------------------------------------------

    def get(self, request):
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        if mode != "subscribe" or not token:
            return HttpResponse("Invalid verification request", status=400)

        if not WhatsAppAccount.objects.filter(
            webhook_verify_token=token, is_active=True
        ).exists():
            logger.warning("Webhook verify_token not found: %s", token)
            return HttpResponse("Forbidden", status=403)

        return HttpResponse(challenge, content_type="text/plain", status=200)

    # ------------------------------------------------------------------
    # POST — inbound message processing
    # ------------------------------------------------------------------

    def post(self, request):
        if not _verify_hmac(request):
            logger.warning("Webhook HMAC validation failed — rejecting POST")
            return HttpResponse("Forbidden", status=403)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse("Bad Request", status=400)

        if payload.get("object") != "whatsapp_business_account":
            # Not a WhatsApp Business webhook — return 200 to avoid Meta retries
            return JsonResponse({"status": "ignored"})

        for (phone_number_id, from_number, sender_name, wamid, text_body) in _parse_inbound_messages(payload):
            try:
                self._handle_message(phone_number_id, from_number, sender_name, wamid, text_body)
            except Exception:
                logger.exception(
                    "Error processing inbound message wamid=%s phone_number_id=%s",
                    wamid, phone_number_id,
                )
                # Keep processing remaining messages; don't 500 to Meta

        return JsonResponse({"status": "ok"})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_message(phone_number_id, from_number, sender_name, wamid, text_body):
        """
        Persist one inbound text message and schedule the WebSocket push task.
        Idempotent: duplicate wamids are silently skipped.
        """
        # 1. Find the receiving WhatsApp account
        try:
            account = WhatsAppAccount.objects.get(phone_number_id=phone_number_id, is_active=True)
        except WhatsAppAccount.DoesNotExist:
            logger.warning("Received webhook for unknown phone_number_id: %s", phone_number_id)
            return

        # 2. Deduplication check before doing any DB writes
        if Message.objects.filter(whatsapp_message_id=wamid).exists():
            logger.debug("Duplicate wamid %s — skipping", wamid)
            return

        # 3. Find or create Contact
        contact, _ = Contact.objects.get_or_create(
            phone_number=from_number,
            defaults={"name": sender_name or ""},
        )
        if sender_name and not contact.name:
            contact.name = sender_name
            contact.save(update_fields=["name"])

        # 4. Find or create Conversation (open one preferred)
        conversation = (
            Conversation.objects
            .filter(contact=contact, whatsapp_account=account, status=Conversation.Status.OPEN)
            .order_by("-created_at")
            .first()
        )
        if conversation is None:
            conversation = Conversation.objects.create(
                contact=contact,
                whatsapp_account=account,
                status=Conversation.Status.OPEN,
            )

        # 5. Create the inbound Message
        message = Message.objects.create(
            conversation=conversation,
            content=text_body,
            direction=Message.Direction.INBOUND,
            whatsapp_message_id=wamid,
            status=Message.Status.DELIVERED,
        )

        # 6. Stamp the conversation
        conversation.touch()

        # 7. Schedule WebSocket push (fire-and-forget)
        process_inbound_message.delay(message.pk)
