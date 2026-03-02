
from __future__ import annotations
from dataclasses import dataclass
from typing import List
from .types import CmdAck, CmdFrame, ProtocolCommand


ACK_BYTE = 0x06  # protocol ACK query 14
HASH = ord("#")

@dataclass
class StreamParser:
    max_len: int = 256

    def __post_init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> List[ProtocolCommand]:
        out: List[ProtocolCommand] = []
        for b in data:
            # ACK is a single-byte query. We only treat it as such when not in a frame.
            if b == ACK_BYTE and len(self.buf) == 0:
                out.append(CmdAck())
                continue

            self.buf.append(b)
            if len(self.buf) > self.max_len:
                # Parse error: drop buffer
                self.buf.clear()
                continue

            if b == HASH:
                raw = bytes(self.buf[:-1])
                self.buf.clear()
                try:
                    text = raw.decode("ascii", errors="strict")
                except UnicodeDecodeError:
                    # invalid frame; ignore
                    continue
                out.append(CmdFrame(text=text + "#"))
        return out

