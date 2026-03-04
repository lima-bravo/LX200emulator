"""
Network-to-serial bridge: listens on a TCP port, connects to a serial port,
and forwards all traffic bidirectionally. Logs every read/write with a
timestamp (Unix time, millisecond accuracy), R or W, and the raw ASCII message.

Usage:
  python -m net2serial_bridge [--host HOST] [--port PORT] [--serial PATH] [--log PATH] [--baud BAUD]
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time

import serial

# Defaults
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 4030
DEFAULT_SERIAL = "/dev/ttyUSB0"
DEFAULT_BAUD = 9600


def _format_log_line(direction: str, data: bytes) -> str:
    """Format one log line: unix timestamp (ms), R or W, colon, raw ASCII message."""
    ts = f"{time.time():.3f}"
    try:
        msg = data.decode("ascii")
    except UnicodeDecodeError:
        msg = data.decode("ascii", errors="replace")
    return f"{ts}:{direction}:{msg}\n"


def _log(log_file, direction: str, data: bytes, lock: threading.Lock) -> None:
    if not data or log_file is None:
        return
    line = _format_log_line(direction, data)
    with lock:
        log_file.write(line)
        log_file.flush()


def _run_client_bridge(
    client: socket.socket,
    ser: serial.Serial,
    log_file,
    log_lock: threading.Lock,
) -> None:
    """Forward between one client and serial until the client disconnects."""
    client.setblocking(True)
    serial_lock = threading.Lock()
    stop = threading.Event()

    def network_to_serial() -> None:
        try:
            while not stop.is_set():
                buf = client.recv(4096)
                if not buf:
                    break
                _log(log_file, "W", buf, log_lock)
                with serial_lock:
                    ser.write(buf)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            stop.set()

    def serial_to_network() -> None:
        try:
            while not stop.is_set():
                with serial_lock:
                    buf = ser.read(4096)
                if not buf:
                    time.sleep(0.01)
                    continue
                _log(log_file, "R", buf, log_lock)
                try:
                    client.sendall(buf)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    break
        except (serial.SerialException, OSError):
            pass
        finally:
            stop.set()

    t1 = threading.Thread(target=network_to_serial, daemon=True)
    t2 = threading.Thread(target=serial_to_network, daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    try:
        client.close()
    except OSError:
        pass


def _quit_listener(quit_event: threading.Event) -> None:
    """Read stdin; set quit_event when user types Q (and Enter)."""
    try:
        while not quit_event.is_set():
            line = input()
            if line.strip().upper() == "Q":
                quit_event.set()
                break
    except (EOFError, OSError):
        quit_event.set()


def run_bridge(
    host: str,
    port: int,
    serial_path: str,
    baud: int,
    log_path: str | None,
) -> None:
    if log_path == "-":
        log_file = sys.stdout
    elif log_path:
        log_file = open(log_path, "a", encoding="utf-8")
    else:
        log_file = None
    log_lock = threading.Lock()
    ser = serial.Serial(serial_path, baudrate=baud, timeout=0.1)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)
    server.settimeout(1.0)

    quit_event = threading.Event()
    quit_thread = threading.Thread(target=_quit_listener, args=(quit_event,), daemon=True)
    quit_thread.start()

    print(
        f"Listening on {host}:{port}, serial {serial_path} @ {baud} baud, log={log_path or 'none'}",
        file=sys.stderr,
    )
    print("Press Q then Enter to quit.", file=sys.stderr)

    try:
        while not quit_event.is_set():
            try:
                client, addr = server.accept()
            except TimeoutError:
                continue
            except OSError as e:
                if quit_event.is_set():
                    break
                print(f"Accept error: {e}", file=sys.stderr)
                break
            print(f"Client connected from {addr}", file=sys.stderr)
            _run_client_bridge(client, ser, log_file, log_lock)
            print("Client disconnected, waiting for next connection.", file=sys.stderr)
    finally:
        server.close()
        ser.close()
        if log_file and log_path and log_path != "-":
            log_file.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Bridge TCP port to serial port; log all traffic with timestamp and R/W."
    )
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"Listen address (default: {DEFAULT_HOST})")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Listen port (default: {DEFAULT_PORT})")
    ap.add_argument("--serial", default=DEFAULT_SERIAL, help=f"Serial port (default: {DEFAULT_SERIAL})")
    ap.add_argument("--log", default=None, help="Log file path; use '-' for stdout (default: no log)")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Serial baud rate (default: {DEFAULT_BAUD})")
    args = ap.parse_args()

    try:
        run_bridge(
            host=args.host,
            port=args.port,
            serial_path=args.serial,
            baud=args.baud,
            log_path=args.log,
        )
    except serial.SerialException as e:
        print(f"Serial error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
