
import asyncio
import pytest

@pytest.mark.asyncio
async def test_ack_returns_mount_mode(tmp_path):
    # Launch server in-process by importing run_server, or run as subprocess in your environment.
    # Here is a simple socket-level test idea; adapt to your test harness.
    import socket
    s = socket.create_connection(("127.0.0.1", 4030))
    s.sendall(bytes([0x06]))  # ACK query 41
    b = s.recv(1)
    assert len(b) == 1
    s.close()

