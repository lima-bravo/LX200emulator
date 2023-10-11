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

    def __init__(self):
        # RA in hours and DEC in degrees
        self.ra=18.91
        self.dec=33.06
        self.mount_mode='A'

    def deg_min_sec(self,degrees):
        deg=int(degrees)
        dmin=60*(degrees-deg)
        min=int(dmin)
        dsec=60*(dmin-min)
        sec=int(dsec)

        return deg, min, sec


    def get_telescope_dec(self):
        deg, min, sec = self.deg_min_sec(self.dec)
        sign='+'
        if self.dec <0:
            sign='-'
        return f'{sign}{deg}*{min}\'{sec}#'

    def get_telescope_ra(self):
        deg, min, sec = self.deg_min_sec(self.ra)
        return f'{deg}:{min}:{sec}#'

    def set_slew_rate_to_centering(self):
        return None # a command that does not require a response

    def process_command(self, command):
        print(f'Command {command}')
        if command == 'A':
            return
        elif command == ':GR':
            return self.get_telescope_ra()
        elif command == ':GD':
            return self.get_telescope_dec()
        elif command == ':RC':
            return self.set_slew_rate_to_centering()
        elif command == 'D':
            return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
        elif command == 'L':
            return 'Lat: 42.3601° N, Long: 71.0589° W'
        else:
            return '\x15'

# Emulated LX200 telescope server
def emulate_telescope(port):
    state_machine = TelescopeStateMachine()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ip_address='0.0.0.0'
    server.bind((ip_address, port))
    server.listen()  # removed the number one

    print(f"Emulated LX200 Telescope server is listening on {ip_address}:{port}")

    while True:
        client_socket, client_address = server.accept()
        print(f"Connected to {client_address[0]}:{client_address[1]}")

        # now send the scope response to confirm the connection


        # client_socket.setblocking(False)

        data_buffer = b''  # set the buffer to empty
        while True:

            try:
                data = client_socket.recv(1024)
                print(data)
                if data == b'' : # empty data received, counter party closed connection
                    break # exit the while loop and close the connection
                else:
                    data_buffer = data.decode() # decode binary string to UTF
                    print(f"data received:[{data_buffer}]")

                    if '#' in data_buffer: # the carriage return signals a command,
                        command = data_buffer.split('#') # split according to the commands

                        for c in command: # run through all the commands
                            # check if the command starts with :
                            if len(c)>1: # make sure it is long enough to hold a command
                                print(f'Processing {c}')
                                if c[0] == ':':
                                    print(f"Received command: {c}")

                                    response = state_machine.process_command(c)
                                    if response is not None:
                                        print(f'Sending response {response}')
                                        client_socket.sendall(response.encode('utf-8'))


            except BlockingIOError as e:
                print(f'{type(e)}')
                # pass

            # time.sleep(0.25)
            time.sleep(0.2)
            # now write to the socket
            # try:
            #     print(f'respond A')
            #     position_update = state_machine.process_command('A')
            #     client_socket.sendall(position_update.encode('utf-8'))
            # except BrokenPipeError:
            #     print("Connection lost")
            #     break

    print(f'Closing socket')
    client_socket.close()

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python script.py <port>")
        sys.exit(1)

    port = int(sys.argv[1])
    emulate_telescope(port)
