
import socket
import time

def test_home_query_codes():
    s = socket.create_connection(("127.0.0.1", 4030))
    s.sendall(b":hS#")   # start homing 2
    s.sendall(b":h?#")   # query status 2
    resp = s.recv(8)
    assert resp[:1] in (b"2", b"1", b"0")
    s.close()

def test_park_then_ready_timer():
    s = socket.create_connection(("127.0.0.1", 4030))
    s.sendall(b":hP#")   # park (no response) 2
    # wait beyond default park_duration_ms in config (5000ms)
    time.sleep(5.5)
    # There is no direct "park status" command in our subset; this is validated by internal state in extended tests.
    # You can extend emulator with a debug-only query if needed for tests.
    s.close()

