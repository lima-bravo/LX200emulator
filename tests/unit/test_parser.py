"""
Unit tests for StreamParser: frame-by-# parsing, single-byte ACK, chunked input, overflow, invalid ASCII.
No TCP or server required.
"""
from __future__ import annotations

import pytest

from scopeboss_emulator.protocol.parser import StreamParser
from scopeboss_emulator.protocol.types import CmdAck, CmdFrame

pytestmark = pytest.mark.unit


def test_feed_empty_returns_empty_list():
    parser = StreamParser(max_len=256)
    assert parser.feed(b"") == []
    assert parser.feed(b"") == []


def test_feed_single_ack_byte_returns_cmd_ack():
    parser = StreamParser(max_len=256)
    out = parser.feed(bytes([0x06]))
    assert len(out) == 1
    assert isinstance(out[0], CmdAck)


def test_feed_hash_terminated_frame_returns_cmd_frame():
    parser = StreamParser(max_len=256)
    out = parser.feed(b":hP#")
    assert len(out) == 1
    assert isinstance(out[0], CmdFrame)
    assert out[0].text == ":hP#"


def test_feed_multiple_frames_returns_all():
    parser = StreamParser(max_len=256)
    out = parser.feed(b":hP#:h?#")
    assert len(out) == 2
    assert out[0].text == ":hP#"
    assert out[1].text == ":h?#"


def test_feed_chunked_frame_assembles_correctly():
    parser = StreamParser(max_len=256)
    assert parser.feed(b":h") == []
    assert parser.feed(b"P#") == [CmdFrame(text=":hP#")]


def test_ack_when_buf_empty_treated_as_single_command():
    parser = StreamParser(max_len=256)
    out = parser.feed(bytes([0x06]))
    assert out == [CmdAck()]


def test_ack_inside_frame_appended_to_buffer():
    parser = StreamParser(max_len=256)
    # 0x06 in middle of frame becomes part of frame (ACK only when buf empty)
    out = parser.feed(b":h" + bytes([0x06]) + b"P#")
    assert len(out) == 1
    assert isinstance(out[0], CmdFrame)
    assert out[0].text == ":h\x06P#"


def test_frame_over_max_len_drops_buffer():
    parser = StreamParser(max_len=5)
    # 6 bytes then '#' -> overflow clears buf, then '#' completes a new frame
    out = parser.feed(b"123456#")
    assert len(out) == 1
    assert out[0].text == "#"  # only the part after overflow forms a frame


def test_invalid_ascii_in_frame_ignored():
    parser = StreamParser(max_len=256)
    # 0xff is not ASCII
    out = parser.feed(b":h\xff#")
    assert out == []


def test_parser_preserves_trailing_hash_in_text():
    parser = StreamParser(max_len=256)
    out = parser.feed(b":F1#")
    assert out[0].text == ":F1#"
