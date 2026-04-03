"""
chat/views.py

Three HTTP views for the agent chat UI:

  GET  /chat/                    – ConversationListView
  GET  /chat/<pk>/               – ConversationDetailView
  POST /chat/<pk>/send/          – SendMessageView  (HTMX)

All views require authentication; queryset-level security ensures an agent
can only see conversations linked to their AccountAssignment records.
"""
import logging

import requests
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views import View
from django.views.generic import ListView

from accounts.mixins import AgentRequiredMixin
from conversations.models import Conversation, Message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conversation list
# ---------------------------------------------------------------------------

class ConversationListView(AgentRequiredMixin, ListView):
    template_name = "chat/list.html"
    context_object_name = "conversations"

    def get_queryset(self):
        return (
            Conversation.objects
            .for_agent(self.request.user)
            .select_related("contact", "whatsapp_account")
            .order_by("-last_message_at")
        )


# ---------------------------------------------------------------------------
# Conversation detail
# ---------------------------------------------------------------------------

class ConversationDetailView(AgentRequiredMixin, View):
    def get(self, request, pk):
        conversation = _get_conversation_or_404(request.user, pk)
        messages = (
            conversation.messages
            .order_by("created_at")
        )
        conversations = (
            Conversation.objects
            .for_agent(request.user)
            .select_related("contact", "whatsapp_account")
            .order_by("-last_message_at")
        )
        return render(request, "chat/detail.html", {
            "conversation": conversation,
            "messages": messages,
            "conversations": conversations,
        })


# ---------------------------------------------------------------------------
# Send message (HTMX endpoint)
# ---------------------------------------------------------------------------

class SendMessageView(AgentRequiredMixin, View):
    """
    POST /chat/<pk>/send/

    Creates an outbound Message, calls the Meta Cloud API, and returns the
    rendered message bubble partial for HTMX to append to the chat window.
    """

    def post(self, request, pk):
        conversation = _get_conversation_or_404(request.user, pk)
        text = request.POST.get("text", "").strip()

        if not text:
            return HttpResponse(status=204)  # nothing to send

        message = Message.objects.create(
            conversation=conversation,
            content=text,
            direction=Message.Direction.OUTBOUND,
            status=Message.Status.PENDING,
        )

        wamid = _send_whatsapp_message(
            account=conversation.whatsapp_account,
            to_number=conversation.contact.phone_number,
            text=text,
        )

        if wamid:
            message.mark_sent(wamid)
        else:
            message.mark_failed()

        conversation.touch()

        return render(request, "chat/partials/message.html", {"message": message})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_conversation_or_404(user, pk):
    try:
        return (
            Conversation.objects
            .for_agent(user)
            .select_related("contact", "whatsapp_account")
            .get(pk=pk)
        )
    except Conversation.DoesNotExist:
        raise Http404


def _send_whatsapp_message(account, to_number: str, text: str) -> str | None:
    """
    Call the Meta WhatsApp Cloud API to send a text message.
    Returns the wamid on success, None on failure.

    Brazilian number note
    --------------------
    Meta webhooks deliver the sender's phone in the normalized 12-digit wa_id
    format (e.g. 554791101906) but the sandbox allowed-recipients list is
    registered with the full 13-digit format (5547991101906).  When we get a
    131030 "not in allowed list" error we automatically retry with the
    13-digit variant so both sandbox and production work without manual DB
    edits.  In production there is no allowed-list restriction so the first
    attempt always succeeds.
    """
    url = f"https://graph.facebook.com/v25.0/{account.phone_number_id}/messages"
    headers = account.get_api_headers()

    def _attempt(number: str):
        payload = {
            "messaging_product": "whatsapp",
            "to": number,
            "type": "text",
            "text": {"body": text},
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        return resp

    try:
        resp = _attempt(to_number)

        # 131030 = sandbox "recipient not in allowed list".
        # Retry once with the 13-digit Brazilian number variant if applicable.
        if resp.status_code == 400:
            err_code = resp.json().get("error", {}).get("code")
            if err_code == 131030 and len(to_number) == 12 and to_number.startswith("55"):
                # Insert the missing 9 after the 2-digit area code: 55AA -> 55AA9XXXXXXXX
                alt_number = to_number[:4] + "9" + to_number[4:]
                logger.debug(
                    "Got 131030 for %s — retrying with Brazilian 13-digit variant %s",
                    to_number, alt_number,
                )
                resp = _attempt(alt_number)

        resp.raise_for_status()
        data = resp.json()
        return data.get("messages", [{}])[0].get("id")
    except Exception:
        logger.exception(
            "Failed to send WhatsApp message to %s via account %s",
            to_number,
            account.pk,
        )
        return None
