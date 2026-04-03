"""
Unit tests for the `conversations` app (Module 3).

Coverage:
- Contact model: creation, display_name, str, phone uniqueness
- Conversation model: creation, status helpers (touch/close/reopen), str, ordering
- Message model: creation, status transition helpers, str
- ContactQuerySet.for_agent: visibility scoping
- ConversationQuerySet.for_agent / .open / .closed: security + filters
- MessageQuerySet.for_agent / .inbound / .outbound: security + direction filters
- Cross-agent isolation: agent A cannot see agent B's conversations
- Admin sees all (unfiltered manager)
- Deduplication: whatsapp_message_id unique constraint
"""
import pytest
from django.db import IntegrityError
from django.utils import timezone

from conversations.models import Contact, Conversation, Message
from whatsapp_config.models import AccountAssignment, WhatsAppAccount

from .conftest import (
    AccountAssignmentFactory,
    AgentUserFactory,
    ContactFactory,
    ConversationFactory,
    MessageFactory,
    WhatsAppAccountFactory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assign(agent, account):
    """Create an AccountAssignment linking agent → account."""
    return AccountAssignment.objects.create(user=agent, account=account)


def _conversation(account, contact=None, status="open", agent=None):
    contact = contact or ContactFactory()
    conv = Conversation.objects.create(
        contact=contact,
        whatsapp_account=account,
        status=status,
        assigned_agent=agent,
    )
    return conv


def _message(conv, direction="inbound", wamid=None, status="sent"):
    return Message.objects.create(
        conversation=conv,
        content="Hello",
        direction=direction,
        whatsapp_message_id=wamid,
        status=status,
    )


# ===========================================================================
# Contact model tests
# ===========================================================================

@pytest.mark.django_db
class TestContactModel:
    def test_create_contact(self):
        c = Contact.objects.create(phone_number="+15550001234", name="Alice")
        assert c.pk is not None
        assert c.phone_number == "+15550001234"

    def test_str_prefers_name(self):
        c = Contact.objects.create(phone_number="+15550001111", name="Bob")
        assert str(c) == "Bob"

    def test_str_fallback_to_phone(self):
        c = Contact.objects.create(phone_number="+15550002222", name="")
        assert str(c) == "+15550002222"

    def test_display_name_prefers_name(self):
        c = Contact.objects.create(phone_number="+15550003333", name="Carol")
        assert c.display_name == "Carol"

    def test_display_name_fallback_to_phone(self):
        c = Contact.objects.create(phone_number="+15550004444", name="")
        assert c.display_name == "+15550004444"

    def test_phone_number_must_be_unique(self):
        Contact.objects.create(phone_number="+15550005555")
        with pytest.raises(IntegrityError):
            Contact.objects.create(phone_number="+15550005555")

    def test_name_is_optional(self):
        c = Contact.objects.create(phone_number="+15550006666")
        assert c.name == ""

    def test_ordering_by_phone_number(self):
        Contact.objects.create(phone_number="+15550009999")
        Contact.objects.create(phone_number="+15550000001")
        phones = list(Contact.objects.values_list("phone_number", flat=True))
        assert phones == sorted(phones)


# ===========================================================================
# Conversation model tests
# ===========================================================================

@pytest.mark.django_db
class TestConversationModel:
    def test_create_conversation(self):
        account = WhatsAppAccountFactory()
        contact = ContactFactory()
        conv = Conversation.objects.create(
            contact=contact,
            whatsapp_account=account,
        )
        assert conv.pk is not None
        assert conv.status == Conversation.Status.OPEN

    def test_str_contains_pk_and_contact(self):
        account = WhatsAppAccountFactory()
        contact = ContactFactory(phone_number="+15557778888", name="")
        conv = Conversation.objects.create(contact=contact, whatsapp_account=account)
        result = str(conv)
        assert str(conv.pk) in result
        assert "+15557778888" in result

    def test_default_status_is_open(self):
        account = WhatsAppAccountFactory()
        contact = ContactFactory()
        conv = Conversation.objects.create(contact=contact, whatsapp_account=account)
        assert conv.status == "open"

    def test_touch_updates_last_message_at(self):
        account = WhatsAppAccountFactory()
        contact = ContactFactory()
        conv = Conversation.objects.create(contact=contact, whatsapp_account=account)
        assert conv.last_message_at is None
        before = timezone.now()
        conv.touch()
        assert conv.last_message_at >= before

    def test_touch_persists_to_db(self):
        account = WhatsAppAccountFactory()
        contact = ContactFactory()
        conv = Conversation.objects.create(contact=contact, whatsapp_account=account)
        conv.touch()
        fetched = Conversation.objects.get(pk=conv.pk)
        assert fetched.last_message_at is not None

    def test_close_sets_status_closed(self):
        account = WhatsAppAccountFactory()
        contact = ContactFactory()
        conv = Conversation.objects.create(contact=contact, whatsapp_account=account)
        conv.close()
        assert conv.status == "closed"
        fetched = Conversation.objects.get(pk=conv.pk)
        assert fetched.status == "closed"

    def test_reopen_sets_status_open(self):
        account = WhatsAppAccountFactory()
        contact = ContactFactory()
        conv = Conversation.objects.create(
            contact=contact, whatsapp_account=account, status="closed"
        )
        conv.reopen()
        assert conv.status == "open"
        fetched = Conversation.objects.get(pk=conv.pk)
        assert fetched.status == "open"

    def test_assigned_agent_is_nullable(self):
        account = WhatsAppAccountFactory()
        contact = ContactFactory()
        conv = Conversation.objects.create(contact=contact, whatsapp_account=account)
        assert conv.assigned_agent is None

    def test_ordering_newest_last_message_first(self):
        account = WhatsAppAccountFactory()
        c1 = ContactFactory(phone_number="+15550010001")
        c2 = ContactFactory(phone_number="+15550010002")
        conv_old = Conversation.objects.create(contact=c1, whatsapp_account=account)
        conv_new = Conversation.objects.create(contact=c2, whatsapp_account=account)
        conv_old.last_message_at = timezone.now() - timezone.timedelta(hours=2)
        conv_old.save(update_fields=["last_message_at"])
        conv_new.last_message_at = timezone.now()
        conv_new.save(update_fields=["last_message_at"])
        first = Conversation.objects.first()
        assert first.pk == conv_new.pk


# ===========================================================================
# Message model tests
# ===========================================================================

@pytest.mark.django_db
class TestMessageModel:
    def _conv(self):
        account = WhatsAppAccountFactory()
        contact = ContactFactory()
        return Conversation.objects.create(contact=contact, whatsapp_account=account)

    def test_create_message(self):
        conv = self._conv()
        msg = Message.objects.create(
            conversation=conv, content="Hi", direction="inbound"
        )
        assert msg.pk is not None
        assert msg.status == Message.Status.PENDING

    def test_str_contains_direction_and_content(self):
        conv = self._conv()
        msg = Message.objects.create(
            conversation=conv, content="Hello world", direction="outbound"
        )
        assert "outbound" in str(msg)
        assert "Hello world" in str(msg)

    def test_str_truncates_long_content(self):
        conv = self._conv()
        long = "x" * 200
        msg = Message.objects.create(conversation=conv, content=long, direction="inbound")
        assert len(str(msg)) < 100

    def test_default_status_is_pending(self):
        conv = self._conv()
        msg = Message.objects.create(conversation=conv, content="x", direction="inbound")
        assert msg.status == "pending"

    def test_mark_sent_updates_status_and_wamid(self):
        conv = self._conv()
        msg = Message.objects.create(conversation=conv, content="x", direction="outbound")
        msg.mark_sent("wamid.ABC123")
        fetched = Message.objects.get(pk=msg.pk)
        assert fetched.status == "sent"
        assert fetched.whatsapp_message_id == "wamid.ABC123"

    def test_mark_delivered(self):
        conv = self._conv()
        msg = Message.objects.create(
            conversation=conv, content="x", direction="outbound",
            whatsapp_message_id="wamid.DEL1",
        )
        msg.mark_delivered()
        assert Message.objects.get(pk=msg.pk).status == "delivered"

    def test_mark_read(self):
        conv = self._conv()
        msg = Message.objects.create(
            conversation=conv, content="x", direction="outbound",
            whatsapp_message_id="wamid.READ1",
        )
        msg.mark_read()
        assert Message.objects.get(pk=msg.pk).status == "read"

    def test_mark_failed(self):
        conv = self._conv()
        msg = Message.objects.create(
            conversation=conv, content="x", direction="outbound",
        )
        msg.mark_failed()
        assert Message.objects.get(pk=msg.pk).status == "failed"

    def test_whatsapp_message_id_unique(self):
        conv = self._conv()
        Message.objects.create(
            conversation=conv, content="a", direction="inbound",
            whatsapp_message_id="wamid.DUPE",
        )
        with pytest.raises(IntegrityError):
            Message.objects.create(
                conversation=conv, content="b", direction="inbound",
                whatsapp_message_id="wamid.DUPE",
            )

    def test_whatsapp_message_id_can_be_null_multiple_times(self):
        """Multiple outbound messages without a wamid yet should not collide."""
        conv = self._conv()
        m1 = Message.objects.create(
            conversation=conv, content="a", direction="outbound", whatsapp_message_id=None
        )
        m2 = Message.objects.create(
            conversation=conv, content="b", direction="outbound", whatsapp_message_id=None
        )
        assert m1.pk != m2.pk

    def test_ordering_oldest_first(self):
        conv = self._conv()
        m1 = Message.objects.create(conversation=conv, content="first", direction="inbound")
        m2 = Message.objects.create(conversation=conv, content="second", direction="outbound")
        messages = list(conv.messages.all())
        assert messages[0].pk == m1.pk
        assert messages[1].pk == m2.pk


# ===========================================================================
# ContactQuerySet.for_agent
# ===========================================================================

@pytest.mark.django_db
class TestContactQuerySetForAgent:
    def test_agent_sees_contacts_in_their_conversations(self):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory()
        _assign(agent, account)
        contact = ContactFactory()
        _conversation(account, contact)
        qs = Contact.objects.for_agent(agent)
        assert contact in qs

    def test_agent_cannot_see_contacts_in_others_conversations(self):
        agent_a = AgentUserFactory()
        agent_b = AgentUserFactory()
        account_a = WhatsAppAccountFactory()
        account_b = WhatsAppAccountFactory()
        _assign(agent_a, account_a)
        _assign(agent_b, account_b)
        contact_b = ContactFactory()
        _conversation(account_b, contact_b)
        qs = Contact.objects.for_agent(agent_a)
        assert contact_b not in qs

    def test_unassigned_agent_sees_no_contacts(self):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory()
        contact = ContactFactory()
        _conversation(account, contact)
        qs = Contact.objects.for_agent(agent)
        assert contact not in qs

    def test_contact_in_multiple_accounts_no_duplication(self):
        agent = AgentUserFactory()
        account1 = WhatsAppAccountFactory()
        account2 = WhatsAppAccountFactory()
        _assign(agent, account1)
        _assign(agent, account2)
        contact = ContactFactory()
        _conversation(account1, contact)
        _conversation(account2, contact)
        qs = Contact.objects.for_agent(agent)
        assert qs.count() == 1  # distinct() prevents duplicates


# ===========================================================================
# ConversationQuerySet.for_agent
# ===========================================================================

@pytest.mark.django_db
class TestConversationQuerySetForAgent:
    def test_agent_sees_own_conversations(self):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory()
        _assign(agent, account)
        conv = _conversation(account)
        qs = Conversation.objects.for_agent(agent)
        assert conv in qs

    def test_agent_cannot_see_other_agents_conversations(self):
        agent_a = AgentUserFactory()
        agent_b = AgentUserFactory()
        account_a = WhatsAppAccountFactory()
        account_b = WhatsAppAccountFactory()
        _assign(agent_a, account_a)
        _assign(agent_b, account_b)
        conv_b = _conversation(account_b)
        qs = Conversation.objects.for_agent(agent_a)
        assert conv_b not in qs

    def test_unassigned_agent_sees_no_conversations(self):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory()
        conv = _conversation(account)
        qs = Conversation.objects.for_agent(agent)
        assert conv not in qs

    def test_for_agent_returns_all_assigned_accounts_conversations(self):
        agent = AgentUserFactory()
        account1 = WhatsAppAccountFactory()
        account2 = WhatsAppAccountFactory()
        _assign(agent, account1)
        _assign(agent, account2)
        conv1 = _conversation(account1)
        conv2 = _conversation(account2)
        qs = Conversation.objects.for_agent(agent)
        assert conv1 in qs
        assert conv2 in qs

    def test_for_agent_no_duplication_with_multiple_assignments(self):
        """A conversation should appear exactly once even if filtered via multiple paths."""
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory()
        _assign(agent, account)
        conv = _conversation(account)
        qs = Conversation.objects.for_agent(agent)
        assert qs.filter(pk=conv.pk).count() == 1

    def test_open_filter(self):
        account = WhatsAppAccountFactory()
        open_conv = _conversation(account, status="open")
        closed_conv = _conversation(account, status="closed")
        qs = Conversation.objects.open()
        assert open_conv in qs
        assert closed_conv not in qs

    def test_closed_filter(self):
        account = WhatsAppAccountFactory()
        open_conv = _conversation(account, status="open")
        closed_conv = _conversation(account, status="closed")
        qs = Conversation.objects.closed()
        assert closed_conv in qs
        assert open_conv not in qs

    def test_for_agent_chained_with_open(self):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory()
        _assign(agent, account)
        open_conv = _conversation(account, status="open")
        closed_conv = _conversation(account, status="closed")
        qs = Conversation.objects.for_agent(agent).open()
        assert open_conv in qs
        assert closed_conv not in qs

    def test_admin_unfiltered_manager_sees_all(self):
        """The default objects manager (no for_agent) returns all conversations."""
        account1 = WhatsAppAccountFactory()
        account2 = WhatsAppAccountFactory()
        conv1 = _conversation(account1)
        conv2 = _conversation(account2)
        qs = Conversation.objects.all()
        assert conv1 in qs
        assert conv2 in qs


# ===========================================================================
# MessageQuerySet.for_agent
# ===========================================================================

@pytest.mark.django_db
class TestMessageQuerySetForAgent:
    def test_agent_sees_messages_in_own_conversations(self):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory()
        _assign(agent, account)
        conv = _conversation(account)
        msg = _message(conv)
        qs = Message.objects.for_agent(agent)
        assert msg in qs

    def test_agent_cannot_see_messages_in_others_conversations(self):
        agent_a = AgentUserFactory()
        agent_b = AgentUserFactory()
        account_a = WhatsAppAccountFactory()
        account_b = WhatsAppAccountFactory()
        _assign(agent_a, account_a)
        _assign(agent_b, account_b)
        conv_b = _conversation(account_b)
        msg_b = _message(conv_b, wamid="wamid.B001")
        qs = Message.objects.for_agent(agent_a)
        assert msg_b not in qs

    def test_inbound_filter(self):
        account = WhatsAppAccountFactory()
        conv = _conversation(account)
        inbound = _message(conv, direction="inbound", wamid="wamid.IN01")
        outbound = _message(conv, direction="outbound", wamid="wamid.OUT01")
        qs = Message.objects.inbound()
        assert inbound in qs
        assert outbound not in qs

    def test_outbound_filter(self):
        account = WhatsAppAccountFactory()
        conv = _conversation(account)
        inbound = _message(conv, direction="inbound", wamid="wamid.IN02")
        outbound = _message(conv, direction="outbound", wamid="wamid.OUT02")
        qs = Message.objects.outbound()
        assert outbound in qs
        assert inbound not in qs

    def test_for_agent_chained_with_inbound(self):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory()
        _assign(agent, account)
        conv = _conversation(account)
        inbound = _message(conv, direction="inbound", wamid="wamid.IN03")
        outbound = _message(conv, direction="outbound", wamid="wamid.OUT03")
        qs = Message.objects.for_agent(agent).inbound()
        assert inbound in qs
        assert outbound not in qs

    def test_for_agent_no_duplication(self):
        agent = AgentUserFactory()
        account = WhatsAppAccountFactory()
        _assign(agent, account)
        conv = _conversation(account)
        msg = _message(conv, wamid="wamid.SINGLE")
        qs = Message.objects.for_agent(agent)
        assert qs.filter(pk=msg.pk).count() == 1


# ===========================================================================
# Cross-agent isolation end-to-end
# ===========================================================================

@pytest.mark.django_db
class TestCrossAgentIsolation:
    """Comprehensive isolation: agent A must never see agent B's data."""

    def setup_agents(self):
        agent_a = AgentUserFactory()
        agent_b = AgentUserFactory()
        account_a = WhatsAppAccountFactory()
        account_b = WhatsAppAccountFactory()
        _assign(agent_a, account_a)
        _assign(agent_b, account_b)
        contact_a = ContactFactory(phone_number="+15550100001")
        contact_b = ContactFactory(phone_number="+15550200002")
        conv_a = _conversation(account_a, contact=contact_a)
        conv_b = _conversation(account_b, contact=contact_b)
        msg_a = _message(conv_a, wamid="wamid.A001")
        msg_b = _message(conv_b, wamid="wamid.B001")
        return agent_a, agent_b, conv_a, conv_b, msg_a, msg_b, contact_a, contact_b

    def test_agent_a_sees_own_conversation(self):
        agent_a, _, conv_a, _, _, _, _, _ = self.setup_agents()
        assert conv_a in Conversation.objects.for_agent(agent_a)

    def test_agent_a_does_not_see_b_conversation(self):
        agent_a, _, _, conv_b, _, _, _, _ = self.setup_agents()
        assert conv_b not in Conversation.objects.for_agent(agent_a)

    def test_agent_b_sees_own_conversation(self):
        _, agent_b, _, conv_b, _, _, _, _ = self.setup_agents()
        assert conv_b in Conversation.objects.for_agent(agent_b)

    def test_agent_b_does_not_see_a_conversation(self):
        _, agent_b, conv_a, _, _, _, _, _ = self.setup_agents()
        assert conv_a not in Conversation.objects.for_agent(agent_b)

    def test_agent_a_does_not_see_b_messages(self):
        agent_a, _, _, _, _, msg_b, _, _ = self.setup_agents()
        assert msg_b not in Message.objects.for_agent(agent_a)

    def test_agent_a_does_not_see_b_contacts(self):
        agent_a, _, _, _, _, _, _, contact_b = self.setup_agents()
        assert contact_b not in Contact.objects.for_agent(agent_a)

    def test_shared_account_both_agents_see_conversation(self):
        """When two agents share the same account, both see the conversation."""
        agent_a = AgentUserFactory()
        agent_b = AgentUserFactory()
        shared_account = WhatsAppAccountFactory()
        _assign(agent_a, shared_account)
        _assign(agent_b, shared_account)
        conv = _conversation(shared_account)
        assert conv in Conversation.objects.for_agent(agent_a)
        assert conv in Conversation.objects.for_agent(agent_b)
