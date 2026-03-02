
from __future__ import annotations
from dataclasses import dataclass
from typing import Union

@dataclass(frozen=True)
class CmdAck:
    """Single-byte ACK (0x06) query."""
    pass

@dataclass(frozen=True)
class CmdFrame:
    """A full '#'-terminated ASCII command frame (decoded)."""
    text: str # includes trailing '#', e.g. ':hP#'

ProtocolCommand = Union[CmdAck, CmdFrame]

