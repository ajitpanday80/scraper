"""Message senders. Only a test sender exists for now: it records the send but contacts nobody.

A real provider (WhatsApp Cloud API with approved templates, or a DLT-registered SMS gateway)
plugs in by adding a class with the same `send` signature to SENDERS.
"""
from dataclasses import dataclass


@dataclass
class Result:
    ok: bool
    ref: str = ""
    error: str = ""
    simulated: bool = False


class TestSender:
    name = "Test mode (nothing is actually sent)"
    simulated = True

    def send(self, phone, text, media, variant):
        return Result(ok=True, ref="test", simulated=True)


SENDERS = {"test": TestSender()}


def get(channel):
    return SENDERS.get(channel) or SENDERS["test"]
