"""
Shared test doubles for the api/messenger.py suites.

These lived in duplicate in test_messenger_gate.py and test_phone_shared_flow.py.
Two copies of a spy drift silently: a spy is only ever asserted against by the
suite that owns it, so a fix or a new recorded field lands in one copy and both
suites stay green while they no longer test the same thing.

Fixtures deliberately stay in the individual suites. They are not duplicates —
the gate suite substitutes a PauseStateSpy for pause_state and drives
bot_is_active per test, while the phone-shared suite uses the real state
modules and pins bot_is_active on for the whole file. Only the doubles are
shared.

tests/ is a package, so both suites import these by name:
    from tests.conftest import GeneratorSpy, PauseStateSpy, SendSpy
"""


class SendSpy:
    """Stands in for api.send_api.send_text_message."""

    def __init__(self):
        self.calls = []

    def __call__(self, recipient_psid, text):
        self.calls.append((recipient_psid, text))
        return True

    @property
    def last_text(self):
        """Text of the most recent send. Raises IndexError if nothing was sent,
        which is a clearer failure than comparing against None."""
        return self.calls[-1][1]


class PauseStateSpy:
    """
    Stands in for api.pause_state. Records reads as well as writes — the
    requirement is 'no pause_state reads or writes', and asserting only on
    stored state would let a stray is_paused() call through.
    """

    def __init__(self):
        self.pause_calls = []
        self.is_paused_calls = []

    def pause_thread(self, customer_id, reason="rep_reply"):
        self.pause_calls.append((customer_id, reason))

    def is_paused(self, customer_id):
        self.is_paused_calls.append(customer_id)
        return False

    @property
    def touched(self):
        return bool(self.pause_calls or self.is_paused_calls)


class GeneratorSpy:
    """
    Stands in for the Generator on app.state. Returns a fixed string so a test
    can inject a reply — a CTA-bearing one, say — without a FAISS index, an
    OPENAI_API_KEY, or a network call.
    """

    def __init__(self, reply="স্টাব উত্তর"):
        self.reply = reply
        self.calls = []

    def generate(self, text):
        self.calls.append(text)
        return self.reply
