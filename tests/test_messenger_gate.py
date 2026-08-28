"""
Tests for the active-hours gate in api/messenger.py
Run with: pytest tests/test_messenger_gate.py -v

Outside the active window the webhook must still ack Facebook with 200 but
do nothing else: no Send API calls, no pause_state reads OR writes, no RAG.

These tests mount the messenger router on a bare FastAPI app with a stub
generator, so they need no FAISS index, no OPENAI_API_KEY, and no network.
"""
import hashlib
import hmac
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import messenger
from tests.conftest import GeneratorSpy, PauseStateSpy, SendSpy

TEST_APP_SECRET = "test-app-secret"
TEST_APP_ID = 111111111111111      # "our" bot's app id
REP_APP_ID = 999999999999999       # a rep replying via Page Inbox
CUSTOMER = "CUSTOMER_PSID_1234567890"
PAGE = "PAGE_PSID_9876543210"


# ============================================================
# Fixtures  (spies live in tests/conftest.py)
# ============================================================
@pytest.fixture
def spies(monkeypatch):
    send, pause, generator = SendSpy(), PauseStateSpy(), GeneratorSpy()
    monkeypatch.setattr(messenger, "send_text_message", send)
    monkeypatch.setattr(messenger, "pause_state", pause)
    monkeypatch.setattr(messenger, "FACEBOOK_APP_SECRET", TEST_APP_SECRET)
    monkeypatch.setattr(messenger, "FACEBOOK_APP_ID", TEST_APP_ID)
    return send, pause, generator


@pytest.fixture
def client(spies):
    _, _, generator = spies
    app = FastAPI()
    app.include_router(messenger.router)
    app.state.generator = generator
    return TestClient(app)


def post_event(client, event):
    """POST one messaging event with a valid HMAC signature."""
    import json

    payload = {"object": "page", "entry": [{"id": PAGE, "time": 0, "messaging": [event]}]}
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(
        TEST_APP_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": f"sha256={signature}",
            "Content-Type": "application/json",
        },
    )


# ============================================================
# The five event shapes that must all be covered by one gate
# ============================================================
TEXT_EVENT = {
    "sender": {"id": CUSTOMER},
    "recipient": {"id": PAGE},
    "message": {"mid": "m1", "text": "প্যাকেজের দাম কত?"},
}
ATTACHMENT_EVENT = {
    "sender": {"id": CUSTOMER},
    "recipient": {"id": PAGE},
    "message": {
        "mid": "m2",
        "attachments": [{"type": "image", "payload": {"url": "https://example.com/a.jpg"}}],
    },
}
STICKER_EVENT = {
    "sender": {"id": CUSTOMER},
    "recipient": {"id": PAGE},
    "message": {
        "mid": "m3",
        "attachments": [{"type": "image", "payload": {"sticker_id": 369239263222822}}],
    },
}
REP_ECHO_EVENT = {
    "sender": {"id": PAGE},
    "recipient": {"id": CUSTOMER},
    "message": {"mid": "m4", "is_echo": True, "app_id": REP_APP_ID, "text": "আসছি"},
}
POSTBACK_EVENT = {
    "sender": {"id": CUSTOMER},
    "recipient": {"id": PAGE},
    "postback": {"title": "Get Started", "payload": "GET_STARTED"},
}

ALL_EVENTS = [
    pytest.param(TEXT_EVENT, id="text"),
    pytest.param(ATTACHMENT_EVENT, id="attachment"),
    pytest.param(STICKER_EVENT, id="sticker"),
    pytest.param(REP_ECHO_EVENT, id="rep_echo"),
    pytest.param(POSTBACK_EVENT, id="postback"),
]


# ============================================================
# Outside the window — inert
# ============================================================
@pytest.mark.parametrize("event", ALL_EVENTS)
def test_outside_active_hours_is_completely_inert(client, spies, monkeypatch, event):
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: False)

    response = post_event(client, event)

    assert response.status_code == 200, "Facebook must still be acked"
    assert response.json()["reason"] == "outside_active_hours"
    assert send.calls == [], "no Send API calls outside the window"
    assert pause.pause_calls == [], "no pause_state writes outside the window"
    assert pause.is_paused_calls == [], "no pause_state reads outside the window"
    assert generator.calls == [], "no RAG outside the window"
    assert not pause.touched


def test_outside_active_hours_does_not_parse_the_body(client, spies, monkeypatch):
    """
    The gate sits before json.loads, so a correctly-signed but unparseable
    body is dropped silently rather than logged as a JSON error.
    """
    send, pause, _ = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: False)

    body = b"not json at all"
    signature = hmac.new(TEST_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": f"sha256={signature}"},
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "outside_active_hours"
    assert send.calls == []
    assert not pause.touched


def test_bad_signature_still_rejected_outside_the_window(client, spies, monkeypatch):
    """The security boundary must not move with the clock."""
    send, pause, _ = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: False)

    response = client.post(
        "/webhook",
        json={"object": "page", "entry": []},
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ignored"}, (
        "a forged request must be rejected as a bad signature, "
        "not silently attributed to the active-hours gate"
    )
    assert body.get("reason") != "outside_active_hours"
    assert send.calls == []
    assert not pause.touched


# ============================================================
# POSITIVE CONTROL — inside the window, behaviour is unchanged.
# Without these, a gate that always returned early would pass every
# assertion above.
# ============================================================
def test_inside_window_text_reaches_the_generator(client, spies, monkeypatch):
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    response = post_event(client, TEXT_EVENT)

    assert response.json() == {"status": "received"}
    assert generator.calls == ["প্যাকেজের দাম কত?"]
    assert send.calls == [(CUSTOMER, "স্টাব উত্তর")]
    assert pause.is_paused_calls == [CUSTOMER]
    assert pause.pause_calls == []


def test_inside_window_attachment_hands_off_and_pauses(client, spies, monkeypatch):
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    post_event(client, ATTACHMENT_EVENT)

    assert send.calls == [(CUSTOMER, messenger.HANDOFF_MESSAGE)]
    assert pause.pause_calls == [(CUSTOMER, "attachment")]
    assert generator.calls == []


def test_inside_window_sticker_thanks_without_pausing(client, spies, monkeypatch):
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    post_event(client, STICKER_EVENT)

    assert send.calls == [(CUSTOMER, messenger.THANKS_MESSAGE)]
    assert pause.pause_calls == []
    assert generator.calls == []


def test_inside_window_acknowledgement_thanks_without_pausing(client, spies, monkeypatch):
    """
    "Ok" is the customer closing the conversation. Before this branch it went
    to the pipeline and came back with an answer that restarted it.

    The generator assertion is the point of the test, not the reply: it is what
    proves retrieval was never reached, and it is what fails if the branch is
    ever moved below the pipeline call.
    """
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    post_event(client, {**TEXT_EVENT, "message": {"mid": "m6", "text": "Ok"}})

    assert send.calls == [(CUSTOMER, messenger.THANKS_MESSAGE)]
    assert generator.calls == [], "an acknowledgement must never reach FAISS"
    assert pause.pause_calls == []


def test_inside_window_emoji_takes_the_emoji_branch_not_the_acknowledgement_one(
    client, spies, monkeypatch, caplog
):
    """
    Both branches send THANKS_MESSAGE, so the reply cannot tell them apart —
    the log line is the only observable difference, and it is what a future
    reader greps for when asking why a message was answered the way it was.

    Emoji-only sits above the acknowledgement branch and wins. That is belt and
    braces: is_acknowledgement("👍") is independently False, because stripping
    the trailing emoji run leaves nothing.
    """
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    with caplog.at_level(logging.INFO):
        post_event(client, {**TEXT_EVENT, "message": {"mid": "m9", "text": "👍"}})

    assert send.calls == [(CUSTOMER, messenger.THANKS_MESSAGE)]
    assert "emoji-only text" in caplog.text
    assert "acknowledgement" not in caplog.text
    assert generator.calls == []
    assert pause.pause_calls == []


def test_inside_window_acknowledgement_with_a_trailing_emoji_thanks(
    client, spies, monkeypatch
):
    """"ok 👍" is the shape this branch was widened to cover."""
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    post_event(client, {**TEXT_EVENT, "message": {"mid": "m10", "text": "ok 👍"}})

    assert send.calls == [(CUSTOMER, messenger.THANKS_MESSAGE)]
    assert generator.calls == []
    assert pause.pause_calls == []


def test_inside_window_acknowledgement_prefixing_a_question_reaches_the_generator(
    client, spies, monkeypatch
):
    """
    Whole-message matching, at the routing level rather than the predicate
    level. "ok koto lagbe?" starts with "ok" and is a real question.
    """
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    post_event(
        client, {**TEXT_EVENT, "message": {"mid": "m7", "text": "ok koto lagbe?"}}
    )

    assert generator.calls == ["ok koto lagbe?"]
    assert send.calls == [(CUSTOMER, "স্টাব উত্তর")]
    assert pause.pause_calls == []


def test_inside_window_attachment_outranks_an_acknowledgement_caption(
    client, spies, monkeypatch
):
    """
    A photo captioned "ok" still needs human review. Every branch above the
    acknowledgement one outranks it; this pins the one that is easiest to
    break by moving the new branch a few lines up.
    """
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    post_event(
        client,
        {
            **ATTACHMENT_EVENT,
            "message": {
                "mid": "m8",
                "text": "ok",
                "attachments": [
                    {"type": "image", "payload": {"url": "https://example.com/a.jpg"}}
                ],
            },
        },
    )

    assert send.calls == [(CUSTOMER, messenger.HANDOFF_MESSAGE)]
    assert pause.pause_calls == [(CUSTOMER, "attachment")]
    assert generator.calls == []


def test_inside_window_rep_echo_pauses_the_thread(client, spies, monkeypatch):
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    post_event(client, REP_ECHO_EVENT)

    assert pause.pause_calls == [(CUSTOMER, "rep_reply")]
    assert send.calls == [], "pausing on a rep reply must not send anything"
    assert generator.calls == []


def test_inside_window_postback_reaches_the_handler(client, spies, monkeypatch):
    """
    Postbacks have no branch of their own today — they fall through to the
    'no text and no attachments' handoff. That is pre-existing behaviour;
    this test pins it so the gate's effect on postbacks is visible.
    """
    send, pause, generator = spies
    monkeypatch.setattr(messenger, "bot_is_active", lambda: True)

    post_event(client, POSTBACK_EVENT)

    assert send.calls == [(CUSTOMER, messenger.HANDOFF_MESSAGE)]
    assert pause.is_paused_calls == [CUSTOMER]
    assert generator.calls == []


# ============================================================
# The gate is scoped to POST /webhook only
# ============================================================
def test_get_webhook_verification_works_outside_the_window(client, monkeypatch):
    """
    Facebook must be able to (re-)verify the webhook at any hour, or the
    integration cannot be set up during the day.
    """
    monkeypatch.setattr(messenger, "bot_is_active", lambda: False)
    monkeypatch.setattr(messenger, "FACEBOOK_VERIFY_TOKEN", "tok")

    response = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "tok",
            "hub.challenge": "challenge-123",
        },
    )

    assert response.status_code == 200
    assert response.text == "challenge-123"
