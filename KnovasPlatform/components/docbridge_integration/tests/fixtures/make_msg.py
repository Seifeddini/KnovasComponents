"""Erzeugt eine minimale, aber echte Outlook-.msg (OLE/CFB) als Testfixture.

Layout nach MS-OXMSG:
  __properties_version1.0        Property-Stream der Nachricht (32-Byte-Kopf)
  __substg1.0_<TAG>              je ein Stream pro variabel langer Property
  __recip_version1.0_#00000000/  Empfaenger-Storage (8-Byte-Property-Kopf)
  __nameid_version1.0/           Named-Property-Map (drei leere Streams)
"""

import datetime
import struct

from extract_msg import OleWriter

PT_LONG = 0x0003
PT_SYSTIME = 0x0040
PT_UNICODE = 0x001F

READABLE_WRITABLE = 0x06

# Festes Datum haelt die Fixture byteweise reproduzierbar.
SENT_AT = datetime.datetime(2026, 3, 15, 10, 0, 0, tzinfo=datetime.timezone.utc)
EPOCH_1601 = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
FILETIME = int((SENT_AT - EPOCH_1601).total_seconds() * 10_000_000)

SUBJECT = "Rückfrage zum Kaufvertrag 2024-001"
BODY = (
    "Guten Tag,\r\n\r\n"
    "der Kaufpreis von EUR 485.000,00 ist bis zum Übergabetermin zu bezahlen.\r\n"
    "Bitte bestätigen Sie den Termin.\r\n\r\n"
    "Freundliche Grüsse\r\nA. Muster"
)
SENDER_NAME = "Anna Muster"
RECIPIENT_EMAIL = "beat.beispiel@example.com"


def _tag_name(propid: int, proptype: int) -> str:
    return f"__substg1.0_{propid:04X}{proptype:04X}"


class _PropStream:
    """Sammelt 16-Byte-Property-Eintraege und die Streams, auf die sie zeigen."""

    def __init__(self, header: bytes):
        self.header = header
        self.entries: list[bytes] = []
        self.streams: dict[str, bytes] = {}

    def add_unicode(self, propid: int, value: str) -> None:
        raw = value.encode("utf-16-le")
        # Das Groessenfeld zaehlt den abschliessenden Null-Terminator mit (2 Byte).
        self.entries.append(
            struct.pack(
                "<IIII", (propid << 16) | PT_UNICODE, READABLE_WRITABLE, len(raw) + 2, 0
            )
        )
        self.streams[_tag_name(propid, PT_UNICODE)] = raw

    def add_long(self, propid: int, value: int) -> None:
        self.entries.append(
            struct.pack("<IIQ", (propid << 16) | PT_LONG, READABLE_WRITABLE, value)
        )

    def add_time(self, propid: int, filetime: int) -> None:
        self.entries.append(
            struct.pack("<IIQ", (propid << 16) | PT_SYSTIME, READABLE_WRITABLE, filetime)
        )

    def properties_bytes(self) -> bytes:
        return self.header + b"".join(self.entries)


def build_sample_msg(path: str) -> None:
    """Schreibt eine gueltige .msg nach ``path``."""
    top = _PropStream(
        struct.pack(
            "<8sIIII8s",
            b"\x00" * 8,   # reserviert
            1,             # naechste Empfaenger-ID
            0,             # naechste Anhang-ID
            1,             # Empfaengeranzahl
            0,             # Anhanganzahl
            b"\x00" * 8,   # reserviert
        )
    )
    top.add_unicode(0x0037, SUBJECT)                     # PR_SUBJECT_W
    top.add_unicode(0x0E1D, SUBJECT)                     # PR_NORMALIZED_SUBJECT_W
    top.add_unicode(0x1000, BODY)                        # PR_BODY_W
    top.add_unicode(0x0C1A, SENDER_NAME)                 # PR_SENDER_NAME_W
    top.add_unicode(0x0C1F, "anna.muster@example.com")   # PR_SENDER_EMAIL_ADDRESS_W
    top.add_unicode(0x001A, "IPM.Note")                  # PR_MESSAGE_CLASS_W
    # extract_msg liest msg.date aus PR_CLIENT_SUBMIT_TIME, nicht aus Delivery-
    # oder Creation-Time -- und nur, wenn die Nachricht als gesendet gilt.
    top.add_time(0x0039, FILETIME)                       # PR_CLIENT_SUBMIT_TIME
    top.add_time(0x0E06, FILETIME)                       # PR_MESSAGE_DELIVERY_TIME
    top.add_time(0x3007, FILETIME)                       # PR_CREATION_TIME
    # isSent prueft PR_MESSAGE_FLAGS auf das MSGFLAG_UNSENT-Bit (0x08).
    top.add_long(0x0E07, 0x01)                           # PR_MESSAGE_FLAGS = READ

    recip = _PropStream(b"\x00" * 8)
    recip.add_unicode(0x3001, "Beat Beispiel")           # PR_DISPLAY_NAME_W
    recip.add_unicode(0x3003, RECIPIENT_EMAIL)           # PR_EMAIL_ADDRESS_W
    recip.add_long(0x0C15, 1)                            # PR_RECIPIENT_TYPE = To

    writer = OleWriter()
    writer.addEntry("__properties_version1.0", top.properties_bytes())
    for name, data in top.streams.items():
        writer.addEntry(name, data)

    writer.addEntry("__recip_version1.0_#00000000", storage=True)
    writer.addEntry(
        "__recip_version1.0_#00000000/__properties_version1.0", recip.properties_bytes()
    )
    for name, data in recip.streams.items():
        writer.addEntry(f"__recip_version1.0_#00000000/{name}", data)

    writer.addEntry("__nameid_version1.0", storage=True)
    for tag in ("00020102", "00030102", "00040102"):
        writer.addEntry(f"__nameid_version1.0/__substg1.0_{tag}", b"")

    writer.write(path)
