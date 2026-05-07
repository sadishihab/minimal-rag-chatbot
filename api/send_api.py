"""
Facebook Send API client.

Wraps the HTTP call to send a message back to a Messenger user.
Pure I/O — no business logic. Used by api/messenger.py to deliver bot replies.
"""
import logging
import requests

from config import FACEBOOK_PAGE_ACCESS_TOKEN

log = logging.getLogger(__name__)

SEND_API_URL = "https://graph.facebook.com/v18.0/me/messages"


def send_text_message(recipient_psid: str, text: str) -> bool:
    """
    Send a plain text message to a Messenger user.

    Args:
        recipient_psid: The Page-Scoped User ID of the recipient (from FB webhook payload)
        text: The message text to send (Bangla or otherwise; FB handles UTF-8)

    Returns:
        True if FB accepted the message, False otherwise.
    """
    if not FACEBOOK_PAGE_ACCESS_TOKEN:
        log.error("FACEBOOK_PAGE_ACCESS_TOKEN is not configured — cannot send")
        return False

    payload = {
        "recipient": {"id": recipient_psid},
        "message": {"text": text},
        "messaging_type": "RESPONSE",  # Required: indicates this is a reply, not a promotional msg
    }

    try:
        response = requests.post(
            SEND_API_URL,
            params={"access_token": FACEBOOK_PAGE_ACCESS_TOKEN},
            json=payload,
            timeout=10,  # FB usually responds in <2s; 10s is generous
        )
    except requests.RequestException as e:
        log.error(f"Send API request failed (network/timeout): {type(e).__name__}: {e}")
        return False

    if response.status_code == 200:
        log.info(f"Send API → {recipient_psid[:10]}... text({len(text)}ch) OK")
        return True

    # Log failure with details
    log.error(
        f"Send API failed: status={response.status_code}, "
        f"response={response.text[:300]}"
    )
    return False