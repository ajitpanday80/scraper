"""Message senders: a test sender (records the send, contacts nobody) and WhatsApp (see whatsapp.py).

Another provider (WhatsApp Cloud API, a DLT-registered SMS gateway) plugs in by adding a class with the same
`send` signature (and optionally `ready()`) and registering it in get() and CHANNELS.
"""
from dataclasses import dataclass


@dataclass
class Result:
    ok: bool
    ref: str = ""
    error: str = ""
    simulated: bool = False
    lead_error: bool = False  # this lead can't be messaged (e.g. not on WhatsApp); the sender itself works


class TestSender:
    name = "Test mode (nothing is actually sent)"
    simulated = True

    def send(self, phone, text, media, variant):
        return Result(ok=True, ref="test", simulated=True)


SENDERS = {"test": TestSender()}
CHANNELS = {"test": "Test mode (nothing is sent)", "whatsapp": "WhatsApp (linked number)"}


def get(channel):
    if channel == "whatsapp" and "whatsapp" not in SENDERS:
        from whatsapp import WhatsAppSender  # lazy: whatsapp imports Result from this module
        SENDERS["whatsapp"] = WhatsAppSender()
    return SENDERS.get(channel) or SENDERS["test"]
