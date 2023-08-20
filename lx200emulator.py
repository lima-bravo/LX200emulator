#
# lx200emulator.py
#
# an emulator for an LX200 connected to a serial port through ethernet
#
# 20280820-2012
# This is the very first version, it connects, that's all... and creates error messages in the app because it does
# not send the right response.
#
import socket
import time
import sys

# Emulated telescope state machine
class TelescopeStateMachine:
    def process_command(self, command):
        if command == 'A':
            return 'RA: 03h 12m 45s, Dec: +12° 30\' 15"'
        elif command == 'D':
            return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
        elif command == 'L':
            return 'Lat: 42.3601° N, Long: 71.0589° W'
        else:
            return 'Unknown command'

# Emulated LX200 telescope server
def emulate_telescope(port):
    state_machine = TelescopeStateMachine()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('127.0.0.1', port))
    server.listen(1)

    print(f"Emulated LX200 Telescope server is listening on 127.0.0.1:{port}")

    while True:
        client_socket, client_address = server.accept()
        print(f"Connected to {client_address[0]}:{client_address[1]}")

        client_socket.setblocking(False)

        data_buffer = b''
        while True:
            try:
                data = client_socket.recv(1024)
                if not data:
                    break

                data_buffer += data
                if b'\n' in data_buffer:
                    command, data_buffer = data_buffer.split(b'\n', 1)
                    command = command.decode('utf-8').strip()
                    print(f"Received command: {command}")

                    response = state_machine.process_command(command)
                    client_socket.sendall(response.encode('utf-8'))

                    position_update = state_machine.process_command('A')
                    client_socket.sendall(position_update.encode('utf-8'))

            except BlockingIOError:
                pass

            time.sleep(0.25)

        client_socket.close()

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python script.py <port>")
        sys.exit(1)

    port = int(sys.argv[1])
    emulate_telescope(port)
