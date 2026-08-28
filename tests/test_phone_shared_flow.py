"""
Tests for the phone-shared flow through api/messenger.py.
Run with: pytest tests/test_phone_shared_flow.py -v

Covers the two things the string tests cannot: WHERE the flag gets set
relative to the routing branches, and that every send site actually goes
through the substitution wrapper.

These mount the messenger router on a bare FastAPI app with a stub generator,
so they need no FAISS index, no OPENAI_API_KEY, and no network.
"""
import hashlib
import hmac
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import messenger, pause_state, phone_shared_state
from api.cta_substitution import CTA_FULL, CTA_SHORT, REPLACEMENT_GENERIC
from generation.phone_detector import PHONE_ACKNOWLEDGMENT
from tests.conftest import GeneratorSpy, SendSpy

TEST_APP_SECRET = "test-app-secret"
TEST_APP_ID = 111111111111111
REP_APP_ID = 999999999999999
CUSTOMER = "CUSTOMER_PSID_1234567890"
OTHER_CUSTOMER = "CUSTOMER_PSID_0987654321"
PAGE = "PAGE_PSID_9876543210"

NUMBER_TEXT = "amar number 01775760496"


# ============================================================
# Fixtures  (spies live in tests/conftest.py)
# ============================================================
@pytest.fixture(autouse=True)
def clean_state():
    """
    A leftover PSID in either state module makes every later test look broken
    for reasons that have nothing to do with the test — the documented
    paused-thread trap, now with a second dict to trip over.
    """
    phone_shared_state.clear_all()
    pause_state.clear_all()
    yield
    phone_shared_state.clear_all()
    pause_state.clear_all()


@pytest.fixture
def send(monkeypatch):
    spy = SendSpy()
    monkeypatch.setattr(messenger, "send_text_message", spy)
    monkeypatch.setattr(messenger, "FACEBOOK_APP_SECRET", TEST_APP_SECRET)
    monkeypatch.setattr(messenger, "FACEBOOK_APP_ID", TEST_APP_ID)
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)
    return spy


@pytest.fixture
def make_client(send):
    def _make(generator=None):
        app = FastAPI()
        app.include_router(messenger.router)
        app.state.generator = generator or GeneratorSpy()
        return TestClient(app)

    return _make


@pytest.fixture
def client(make_client):
    return make_client()


def post_event(client, event):
    """POST one messaging event with a valid HMAC signature."""
    payload = {"object": "page", "entry": [{"id": PAGE, "time": 0, "messaging": [event]}]}
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(TEST_APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": f"sha256={signature}",
            "Content-Type": "application/json",
        },
    )


def text_event(text, sender=CUSTOMER):
    return {
        "sender": {"id": sender},
        "recipient": {"id": PAGE},
        "message": {"mid": "m", "text": text},
    }


def attachment_event(sender=CUSTOMER):
    return {
        "sender": {"id": sender},
        "recipient": {"id": PAGE},
        "message": {
            "mid": "m",
            "attachments": [{"type": "image", "payload": {"url": "https://example.com/a.jpg"}}],
        },
    }


# ============================================================
# Where the flag gets set
# ============================================================
def test_plain_number_sets_the_flag(client):
    post_event(client, text_event(NUMBER_TEXT))
    assert phone_shared_state.has_shared_phone(CUSTOMER)


def test_number_alongside_a_url_still_sets_the_flag(client, send):
    """
    The URL branch returns before the generator is ever reached, so a flag set
    only after generate() would miss this entirely.
    """
    post_event(client, text_event("amar number 01775760496, dekhen: fb.com/x"))

    assert phone_shared_state.has_shared_phone(CUSTOMER)
    assert send.last_text == messenger.HANDOFF_MESSAGE_PHONE_SHARED


def test_number_sent_into_a_paused_thread_is_still_recorded(client, send):
    """
    The bot must stay silent — but the pause expires and the bot comes back,
    and at that point it must not re-ask. This is why the detection sits above
    the pause check rather than below it.
    """
    pause_state.pause_thread(CUSTOMER, reason="rep_reply")
    post_event(client, text_event(NUMBER_TEXT))

    assert send.calls == []                          # still silent
    assert phone_shared_state.has_shared_phone(CUSTOMER)


def test_echo_carrying_a_number_does_not_flag_anyone():
    """
    In an echo the text is the page's own outgoing message. Detecting there
    would credit the number to the wrong party — or to the page itself.
    """
    messenger.process_messaging_event(
        {
            "sender": {"id": PAGE},
            "recipient": {"id": CUSTOMER},
            "message": {
                "mid": "m",
                "is_echo": True,
                "app_id": REP_APP_ID,
                "text": "আপনার নম্বর 01775760496 পেয়েছি",
            },
        },
        GeneratorSpy(),
    )

    assert not phone_shared_state.has_shared_phone(CUSTOMER)
    assert not phone_shared_state.has_shared_phone(PAGE)


def test_generator_bypass_also_sets_the_flag(make_client):
    """
    The second, overlapping signal: generate() returning PHONE_ACKNOWLEDGMENT
    is it reporting that its own detector fired on the sanitised text. Proven
    on a message messenger's raw-text check does not catch.
    """
    client = make_client(GeneratorSpy(reply=PHONE_ACKNOWLEDGMENT))
    post_event(client, text_event("এখানে কোনো নম্বর নেই"))

    assert phone_shared_state.has_shared_phone(CUSTOMER)


def test_the_flag_does_not_leak_to_another_customer(make_client):
    client = make_client(GeneratorSpy(reply="দাম ৯ লাখ। " + CTA_FULL))
    post_event(client, text_event(NUMBER_TEXT, sender=CUSTOMER))
    post_event(client, text_event("দাম কত?", sender=OTHER_CUSTOMER))

    assert phone_shared_state.has_shared_phone(CUSTOMER)
    assert not phone_shared_state.has_shared_phone(OTHER_CUSTOMER)


# ============================================================
# What the customer actually receives
# ============================================================
def test_reply_after_sharing_has_the_cta_substituted(make_client, send):
    """The end-to-end assertion: two turns, and the second one is clean."""
    client = make_client(GeneratorSpy(reply="দাম ৯ লাখ টাকা। " + CTA_FULL))

    post_event(client, text_event(NUMBER_TEXT))
    post_event(client, text_event("দাম কত?"))

    assert send.last_text == "দাম ৯ লাখ টাকা। " + REPLACEMENT_GENERIC
    assert CTA_FULL not in send.last_text


def test_reply_before_sharing_is_untouched(make_client, send):
    """
    POSITIVE CONTROL. If substitution ran unconditionally this would go red,
    and every other assertion in this file would still pass.
    """
    reply = "দাম ৯ লাখ টাকা। " + CTA_FULL
    client = make_client(GeneratorSpy(reply=reply))

    post_event(client, text_event("দাম কত?"))

    assert send.last_text == reply
    assert CTA_FULL in send.last_text


def test_attachment_handoff_after_sharing_uses_the_phone_shared_message(client, send):
    """
    The most likely route to the bug: number, then a floorplan photo thirty
    seconds later, and the bot's next words ask for the number again.
    """
    post_event(client, text_event(NUMBER_TEXT))
    post_event(client, attachment_event())

    assert send.last_text == messenger.HANDOFF_MESSAGE_PHONE_SHARED
    assert "মোবাইল নম্বর" not in send.last_text


def test_attachment_handoff_in_a_paused_thread_is_also_substituted(client, send):
    """That send site is inside the pause branch and is easy to miss."""
    post_event(client, text_event(NUMBER_TEXT))
    pause_state.pause_thread(CUSTOMER, reason="rep_reply")
    post_event(client, attachment_event())

    assert send.last_text == messenger.HANDOFF_MESSAGE_PHONE_SHARED


def test_crash_fallback_after_sharing_is_substituted(make_client, send):
    """
    The crash path never reaches generate()'s return value, but it does reach
    the send boundary — which is the whole reason the wrapper sits there.
    """

    class Boom:
        def generate(self, text):
            raise RuntimeError("boom")

    client = make_client(Boom())
    post_event(client, text_event(NUMBER_TEXT))
    post_event(client, text_event("দাম কত?"))

    assert send.last_text == "এই মুহূর্তে একটু সমস্যা হচ্ছে। " + REPLACEMENT_GENERIC
    assert CTA_SHORT not in send.last_text


def test_thanks_message_passes_through_unchanged(client, send):
    """A no-op path — proves the wrapper does not mangle text with no CTA."""
    post_event(client, text_event(NUMBER_TEXT))
    post_event(client, text_event("❤️❤️"))

    assert send.last_text == messenger.THANKS_MESSAGE


def test_drift_warning_reaches_the_log_through_the_real_send_path(make_client, send, caplog):
    """
    Wires the instrument end to end. A model paraphrase reaches the customer
    unchanged — that is accepted — but it must not reach them silently.
    """
    reworded = CTA_FULL.replace("সহায়তা", "সহযোগিতা")
    client = make_client(GeneratorSpy(reply="দাম ৯ লাখ। " + reworded))

    post_event(client, text_event(NUMBER_TEXT))
    with caplog.at_level(logging.WARNING):
        post_event(client, text_event("দাম কত?"))

    assert "CTA drift" in caplog.text
    assert send.last_text == "দাম ৯ লাখ। " + reworded
