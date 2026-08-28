"""
Facebook Messenger webhook endpoints.

This module handles all FB-specific HTTP traffic:
- GET /webhook: verification handshake (called by FB once during setup)
- POST /webhook: incoming customer messages (called by FB on every event)

It does NOT handle the bot's reply generation — that stays in generation/generator.py.
This module is the "messenger-shaped front door" that adapts FB's protocol to
our existing bot.
"""
import hmac
import hashlib
import json
import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

from config import (
    FACEBOOK_VERIFY_TOKEN,
    FACEBOOK_APP_SECRET,
    FACEBOOK_APP_ID,
)
from api.active_hours import bot_is_active, describe_schedule
from api.send_api import send_text_message
from api import pause_state
from api import phone_shared_state
from api.cta_substitution import substitute_cta, check_for_drift
from api.message_classifier import (
    is_emoji_only,
    is_acknowledgement,
    is_all_stickers,
)

# Two imports from generation/ that are NOT reply generation: a pure regex
# predicate and the constant it maps to. Detection has to happen here because
# this is the only layer that holds a PSID — Generator.generate() takes a
# bare string and stays identity-free by design.
from generation.phone_detector import contains_phone_number, PHONE_ACKNOWLEDGMENT

log = logging.getLogger(__name__)

# ============================================================
# Handoff message and URL detection
# ============================================================

# Canned message sent when customer shares an attachment or URL.
# These cases need human review, so bot acknowledges and steps aside.
HANDOFF_MESSAGE = (
    "শেয়ার করার জন্য ধন্যবাদ। "
    "আমরা রিভিউ করে এ বিষয়ে আপনাকে বিস্তারিত জানাচ্ছি। "
    "আর আপনার মোবাইল নম্বরটি শেয়ার করলে আমাদের একজন প্রতিনিধি "
    "আপনাকে কল করে বিস্তারিত তথ্য ও পরামর্শ দিতে পারবেন।"
)

# Same handoff, for a customer who has already given us their number.
# Not a clause swap: HANDOFF_MESSAGE already contains "এ বিষয়ে ... বিস্তারিত
# জানাচ্ছি", so substituting the generic replacement into it produced two
# adjacent sentences both opening "এ বিষয়ে" and both ending "বিস্তারিত জানা-".
# The whole message is replaced instead.
HANDOFF_MESSAGE_PHONE_SHARED = (
    "শেয়ার করার জন্য ধন্যবাদ। "
    "আমরা রিভিউ করে এ বিষয়ে আপনাকে বিস্তারিত জানাচ্ছি।"
)

# Acknowledgment for emoji-only text, acknowledgement text ("ok", "thanks",
# "আচ্ছা"), and sticker-only attachments.
# Customer expressed engagement without asking a question — short, polite,
# no pause (so the bot stays active for any follow-up question).
THANKS_MESSAGE = "ধন্যবাদ"

# URL detection: matches http://, https://, www., or anything with a domain pattern.
# Used to trigger handoff for messages containing links.
import re
URL_PATTERN = re.compile(
    r"(?i)\b("
    r"https?://[^\s]+"           # http:// or https://
    r"|www\.[^\s]+"              # www.something
    r"|[a-zA-Z0-9-]+\.(com|net|org|bd|io|co|ai|app|info)\b[^\s]*"  # bare domain.com
    r")",
    re.IGNORECASE,
)


def contains_url(text: str) -> bool:
    """Return True if text contains anything that looks like a URL."""
    return bool(URL_PATTERN.search(text or ""))


# ============================================================
# Outbound send — the one place a PSID and a reply exist together
# ============================================================
def send_reply(sender_id: str, text: str) -> None:
    """
    Send `text` to `sender_id`, stripping the phone-number CTA first if that
    customer has already given us their number.

    Every send in process_messaging_event goes through here rather than only
    the RAG reply. Seven separate producers can emit the ask — the three
    knowledge base wordings, prompt_builder's ambiguous-query and KB-miss
    instructions, the crash fallback below, and HANDOFF_MESSAGE — and only the
    last of those is a code constant this module can see. Gating at the send
    boundary covers all seven by construction, and is a no-op on text that
    carries no CTA (THANKS_MESSAGE).
    """
    if phone_shared_state.has_shared_phone(sender_id):
        # Whole-message swap, not a substitution — see the constant's comment.
        if text == HANDOFF_MESSAGE:
            text = HANDOFF_MESSAGE_PHONE_SHARED
        text = substitute_cta(text)
        check_for_drift(text, sender_id)

    send_text_message(sender_id, text)


router = APIRouter(prefix="/webhook", tags=["messenger"])


# ============================================================
# GET /webhook — verification handshake
# ============================================================
@router.get("")
async def verify_webhook(request: Request):
    """
    Facebook's webhook verification handshake.
    See full docstring in original code — unchanged.
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    log.info(
        f"GET /webhook verification attempt: "
        f"mode={mode!r}, token_present={token is not None}, "
        f"challenge_present={challenge is not None}"
    )

    if mode == "subscribe" and token == FACEBOOK_VERIFY_TOKEN:
        log.info("Webhook verification SUCCESS")
        return PlainTextResponse(content=challenge or "")

    log.warning(
        f"Webhook verification FAILED: "
        f"mode_match={mode == 'subscribe'}, token_match={token == FACEBOOK_VERIFY_TOKEN}"
    )
    raise HTTPException(status_code=403, detail="verification failed")


# ============================================================
# HMAC-SHA256 signature verification
# ============================================================
def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Verify a webhook POST is genuinely from Facebook."""
    if not signature_header:
        log.warning("Missing X-Hub-Signature-256 header")
        return False

    if not signature_header.startswith("sha256="):
        log.warning(f"Unexpected signature format: {signature_header[:20]}...")
        return False

    if not FACEBOOK_APP_SECRET:
        log.error("FACEBOOK_APP_SECRET is not configured — cannot verify signatures")
        return False

    received_signature = signature_header[len("sha256="):]
    expected_signature = hmac.new(
        key=FACEBOOK_APP_SECRET.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if hmac.compare_digest(received_signature, expected_signature):
        return True

    log.warning("Signature mismatch (request body may have been tampered with)")
    return False


# ============================================================
# Event processing
# ============================================================
def process_messaging_event(event: dict, generator) -> None:
    """
    Process a single messaging event from a FB webhook payload.

    Routing:
      - Echo (our own message echoed back) → skip
      - Attachment (image, video, audio, file, sticker, GIF, location) → handoff message
      - Text containing URL → handoff message
      - Plain text → bot generates Bangla reply

    Args:
        event: One messaging event from FB's payload
        generator: The bot Generator instance (passed in from app.state)
    """
    sender_id = event.get("sender", {}).get("id")
    message = event.get("message", {})

    # ECHO HANDLING
    # Echo events arrive when ANY message goes out from the page — both bot replies
    # and rep replies typed in Page Inbox. We tell them apart by app_id:
    #   - app_id == FACEBOOK_APP_ID  → our bot's own message, ignore
    #   - app_id != FACEBOOK_APP_ID  → a rep replied via Page Inbox → pause this thread
    if message.get("is_echo"):
        echo_app_id = message.get("app_id")
        # In echoes, sender = page, recipient = customer. We pause on the customer ID.
        recipient_id = event.get("recipient", {}).get("id")

        if echo_app_id != FACEBOOK_APP_ID and recipient_id:
            pause_state.pause_thread(recipient_id, reason="rep_reply")
        else:
            log.debug(f"Bot echo (app_id={echo_app_id}) — ignored")
        return

    if not sender_id:
        log.warning(f"Event missing sender.id: {event}")
        return

    # PHONE-SHARED DETECTION
    # Sits above every branch below, because a customer can share a number on
    # any of them — with a URL, as an image caption, or into a paused thread —
    # and only the last branch reaches the generator's own detector.
    #
    # Above the pause check on purpose: a number sent while a rep has the
    # thread still has to be remembered, since the pause expires and the bot
    # comes back. This writes state on a path that otherwise only reads, but
    # it emits nothing, so "paused = silent" still holds.
    #
    # Below the echo branch on purpose: in an echo the text is the page's own
    # outgoing message, so detecting there would credit the wrong party.
    #
    # Detection runs on the RAW text while the generator's runs on sanitised,
    # truncated text. Messenger therefore sees strictly more, and the extra
    # cases suppress an ask rather than emit one — the safe direction.
    if contains_phone_number(message.get("text") or ""):
        phone_shared_state.mark_phone_shared(sender_id, reason="customer_message")

    # PAUSE CHECK — if a rep recently replied to this customer, the bot stays
    # silent for text messages but still acknowledges attachments (so the
    # customer knows their image was received and the rep can see it).
    if pause_state.is_paused(sender_id):
        attachments = message.get("attachments") or []
        if attachments:
            log.info(
                f"Customer {sender_id[:10]}... sent attachment during paused thread → handoff only"
            )
            send_reply(sender_id, HANDOFF_MESSAGE)
        else:
            log.info(
                f"Customer {sender_id[:10]}... sent text during paused thread → bot SILENT"
            )
        return

    # ATTACHMENT DETECTION
    # FB sends message.attachments as a list. We split into two cases:
    #   - Sticker-only attachments → polite "ধন্যবাদ" (no pause, no rep needed)
    #   - Anything else (image, video, audio, file, mixed) → handoff + pause
    attachments = message.get("attachments") or []
    if attachments:
        if is_all_stickers(attachments):
            log.info(f"Customer {sender_id[:10]}... sent sticker(s) → thanks (no pause)")
            send_reply(sender_id, THANKS_MESSAGE)
            return

        att_types = [a.get("type", "unknown") for a in attachments]
        log.info(
            f"Customer {sender_id[:10]}... sent attachment(s): {att_types} "
            f"→ handoff + pause"
        )
        send_reply(sender_id, HANDOFF_MESSAGE)
        pause_state.pause_thread(sender_id, reason="attachment")
        return

    # No attachment, but maybe no text either (rare edge case — sticker without attachment, etc.)
    text = message.get("text")
    if not text:
        log.info(f"Event has no text and no attachments: {event} → handoff")
        send_reply(sender_id, HANDOFF_MESSAGE)
        return

    # URL DETECTION in text
    # Any URL triggers handoff AND pauses the thread — same rationale as attachments,
    # the customer is sharing something that needs human review.
    if contains_url(text):
        log.info(f"Customer {sender_id[:10]}... sent URL in text → handoff + pause")
        send_reply(sender_id, HANDOFF_MESSAGE)
        pause_state.pause_thread(sender_id, reason="url")
        return

        # EMOJI-ONLY TEXT
    # Customer sent only emoji (❤️, 👍, 😊, etc.) — no real question to answer.
    # Acknowledge politely without burning OpenAI tokens or calling the rep.
    if is_emoji_only(text):
        log.info(f"Customer {sender_id[:10]}... sent emoji-only text → thanks (no pause)")
        send_reply(sender_id, THANKS_MESSAGE)
        return

    # ACKNOWLEDGEMENT TEXT
    # "ok" / "thanks" / "আচ্ছা" — the customer is closing the conversation, not
    # asking anything. Same treatment as emoji-only: acknowledge, no pause, no
    # retrieval. Sits here, as the last branch before the pipeline, because
    # every branch above it outranks a polite ack — a rep owns a paused thread,
    # an attachment or a URL needs human review whatever the caption says.
    #
    # is_acknowledgement matches the WHOLE message only, so "ok koto lagbe?"
    # and "ok 01775760496" both fall through to the pipeline, which is what
    # keeps a question (or a shared number) from being answered with "ধন্যবাদ".
    if is_acknowledgement(text):
        log.info(
            f"Customer {sender_id[:10]}... sent acknowledgement {text!r} "
            f"→ thanks (no pause)"
        )
        send_reply(sender_id, THANKS_MESSAGE)
        return

        # Normal text message — generate bot reply
    log.info(f"Customer {sender_id[:10]}... sent: {text[:200]}")
    try:
        reply_text = generator.generate(text)
    except Exception as e:
        log.exception(f"Generator failed for sender {sender_id}: {e}")
        reply_text = (
            "এই মুহূর্তে একটু সমস্যা হচ্ছে। "
            "আপনার মোবাইল নম্বরটি শেয়ার করলে আমাদের সাপোর্ট ম্যানেজার "
            "আপনাকে কল করে সহায়তা করতে পারবেন।"
        )

    # The generator's own detector fired on its sanitised view of the text.
    # Exact comparison against the imported constant, so no detection logic is
    # duplicated — this is generate() reporting what it did, not a re-check.
    if reply_text == PHONE_ACKNOWLEDGMENT:
        phone_shared_state.mark_phone_shared(sender_id, reason="generator_bypass")

    send_reply(sender_id, reply_text)


# ============================================================
# POST /webhook — receive messages from Facebook
# ============================================================
@router.post("")
async def receive_webhook(request: Request):
    """
    Receive a Messenger event from Facebook.

    Flow:
      1. Read raw body (as bytes — required for HMAC verification)
      2. Verify HMAC signature
      2b. Active-hours gate — outside the window, ack and do nothing else
      3. Parse JSON payload
      4. Loop over events, process each
      5. Return 200 OK

    FB requires us to ack within ~20 seconds.
    """
    raw_body = await request.body()

    # Verify HMAC
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(raw_body, signature):
        log.warning("POST /webhook rejected — invalid signature")
        return JSONResponse(status_code=200, content={"status": "ignored"})

    # ACTIVE-HOURS GATE
    # Placed here — after signature verification, before JSON parsing —
    # because every event type (text, attachment, sticker, echo, postback)
    # reaches us through the entry[].messaging[] loop below, so gating above
    # that loop covers all of them by construction, including event types
    # process_messaging_event does not branch on yet.
    #
    # Signature verification stays first: it is pure computation with no
    # sends and no state mutation, so it does not break "inert", and it means
    # forged requests are rejected identically at every hour of the day.
    # Nothing past this point needs the parsed body, so we do not parse it.
    if not bot_is_active():
        # describe_schedule() rather than the window alone: the moment
        # BOT_ALWAYS_ACTIVE_DAYS is set, a window-only message misstates the
        # rule that was actually applied. This line is what someone reads
        # when asking "why was the bot silent on Friday".
        log.info(
            f"Outside active hours ({describe_schedule()}) — webhook event skipped"
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": "outside_active_hours"},
        )

    # Parse JSON
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as e:
        log.error(f"POST /webhook — failed to parse JSON: {e}")
        return JSONResponse(status_code=200, content={"status": "ignored"})

    # FB sends "object": "page" for Messenger Page events
    if payload.get("object") != "page":
        log.info(f"Ignoring non-page event: object={payload.get('object')}")
        return JSONResponse(status_code=200, content={"status": "ignored"})

    # Loop over entries → messaging events
    # Structure: payload.entry[i].messaging[j] = single event
    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            try:
                process_messaging_event(event, request.app.state.generator)
            except Exception as e:
                # Don't let one bad event crash the whole webhook.
                # Log it and keep processing the rest.
                log.exception(f"Error processing event: {e}")

    return JSONResponse(status_code=200, content={"status": "received"})