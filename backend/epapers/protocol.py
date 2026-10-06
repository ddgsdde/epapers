"""Wire v1: little endian, bounded frames; shared with portable C tag core."""

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

MAGIC = b"EP"
VERSION = 1
MAX_PAYLOAD = 192
HEADER = struct.Struct("<2sBBIHH")


class Command(IntEnum):
    INFO = 1
    TIME = 2
    BEGIN = 3
    DATA = 4
    COMMIT = 5
    STATUS = 6
    CONFIG = 7
    ABORT = 8
    REFRESH = 9


@dataclass
class Packet:
    command: int
    transaction: int
    sequence: int
    payload: bytes

    def encode(self):
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError("payload exceeds protocol limit")
        header = HEADER.pack(
            MAGIC, VERSION, self.command, self.transaction, self.sequence, len(self.payload)
        )
        return header + self.payload + struct.pack("<I", zlib.crc32(header + self.payload))

    @classmethod
    def decode(cls, data):
        if len(data) < HEADER.size + 4:
            raise ValueError("truncated packet")
        magic, version, command, txn, seq, length = HEADER.unpack_from(data)
        if (
            magic != MAGIC
            or version != VERSION
            or length > MAX_PAYLOAD
            or len(data) != HEADER.size + length + 4
        ):
            raise ValueError("invalid header")
        if zlib.crc32(data[:-4]) != struct.unpack_from("<I", data, len(data) - 4)[0]:
            raise ValueError("CRC mismatch")
        return cls(command, txn, seq, data[HEADER.size : -4])


def pack_mono(image):
    """MSB first; 1=white; each logical row padded white to a byte boundary."""
    mono = image.convert("1", dither=0)
    w, h = mono.size
    stride = (w + 7) // 8
    result = bytearray(b"\xff" * (stride * h))
    for y in range(h):
        for x in range(w):
            if not mono.getpixel((x, y)):
                result[y * stride + x // 8] &= ~(0x80 >> (x % 8))
    return bytes(result)


def packets_for_blob(blob, transaction, mtu=247):
    chunk = min(MAX_PAYLOAD, mtu - 3 - HEADER.size - 4)
    if chunk < 8:
        raise ValueError("MTU must be negotiated to at least 27")
    begin = struct.pack("<II", len(blob), zlib.crc32(blob))
    yield Packet(Command.BEGIN, transaction, 0, begin)
    for n, offset in enumerate(range(0, len(blob), chunk), 1):
        yield Packet(Command.DATA, transaction, n, blob[offset : offset + chunk])
    yield Packet(Command.COMMIT, transaction, n + 1 if blob else 1, b"")
