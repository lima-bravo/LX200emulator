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
import datetime
import sys


# Emulated telescope state machine


class TelescopeStateMachine:

    def __init__(self):
        # RA in hours and DEC in degrees
        self.ra=18.91
        self.dec=33.06
        self.mount_mode='A'
        self.find_field_diameter=15
        self.display = 'Current display'

    def deg_min_sec(self,degrees):
        deg=int(degrees)
        dmin=60*(degrees-deg)
        min=int(dmin)
        dsec=60*(dmin-min)
        sec=int(dsec)

        return deg, min, sec

    def nack(self): # return a 'telescope is busy' response
        return '\x15'

    # +-----------------------------------------------------------------------------------------------------------+
    # function block Telescope Information ':A'
    # +-----------------------------------------------------------------------------------------------------------+

    # +-----------------------------------------------------------------------------------------------------------+
    # function block Display Information ':E'
    # +-----------------------------------------------------------------------------------------------------------+

    def basic_display(self):
        # create a timestamp display to return to
        # current_time = datetime.datetime.now().strftime('%Y-%m-%d@%H:%M:%S')
        # return a string that ScopeBoss will recognize
        return f'Object'

    def get_keypress(self,command):
        target = command[3:] # get the keypress to the end
        print(f'{sys._getframe().f_code.co_name} Match {target}')

        match target:
            case '9' :
                return 'Mode#'
            case _ :
                return self.basic_display()


    def get_handset_display(self,command):
        target = command[1:3]
        print(f'{sys._getframe().f_code.co_name} Match {target}')
        match target:
            case 'ED' :
                return self.basic_display() # return the state of the display, the state engine should start covering this
            case 'EK' :
                return self.get_keypress(command)
            case _ :
                return 'Select#'
    # +-----------------------------------------------------------------------------------------------------------+
    # function block Telescope Information ':G'
    # +-----------------------------------------------------------------------------------------------------------+

    def get_alignment_menu_entry(self):
        return '1#'

    def get_telescope_dec(self):
        deg, min, sec = self.deg_min_sec(self.dec)
        sign = '+'
        if self.dec < 0:
            sign = '-'
        return f'{sign}{deg}*{min}\'{sec}#'

    def get_find_field_diameter(self):
        return '%03d#'.format(self.find_field_diameter)

    def get_telescope_ra(self):
        deg, min, sec = self.deg_min_sec(self.ra)
        return f'{deg}:{min}:{sec}#'

    def get_scope_status(self):
        return '006#'

    def process_telescope_information(self,command):
        target=command[1:3]
        print(f'{sys._getframe(  ).f_code.co_name} Match {target}')
        match target:
            case 'G0':
                return self.get_alignment_menu_entry()
            case 'GD':
                return self.get_telescope_dec()
            case 'GF':
                return self.get_find_field_diameter()
            case 'GR':
                return self.get_telescope_ra()
            case 'GW':
                return self.get_scope_status()
            case _:
                return self.nack()

    # +-----------------------------------------------------------------------------------------------------------+
    # function block Slew Rate ':R'
    # +-----------------------------------------------------------------------------------------------------------+
    def set_slew_rate_to_centering(self):
        return None  # a command that does not require a response

    def process_slew_rate(self, command):
        target=command[1:3]
        print(f'{sys._getframe(  ).f_code.co_name} Match {target}')
        match target:
            case 'RC':
                return self.set_slew_rate_to_centering()
            case _:
                return self.nack()

    # +-----------------------------------------------------------------------------------------------------------+
    # function block telescope Set commands ':S'
    # +-----------------------------------------------------------------------------------------------------------+
    def set_find_field_diameter(self,command):
        diameter=int(command[3:6])
        print(f'Field diameter set to {diameter}')
        self.find_field_diameter=diameter
        # if field diameter is valid return 1, else return 0
        # assume that the value is always correct
        return '1'



    def process_telescope_set(self, command):
        target=command[1:3]
        match target:
            case 'SF':
                return self.set_find_field_diameter(command)
            case _:
                return self.nack()

    # +-----------------------------------------------------------------------------------------------------------+
    # function block command Tree
    # +-----------------------------------------------------------------------------------------------------------+

    def process_command(self, command):
        target = command[0:2]
        print(f'{sys._getframe(  ).f_code.co_name} Match {target}')
        match target:
            case ':A':
                return self.process_alignment(command)
            case ':E':
                return self.get_handset_display(command)
            case ':G':
                return self.process_telescope_information(command)
            case ':R':
                return self.process_slew_rate(command)
            case ':S':
                return self.process_telescope_set(command)
            case _:
                return self.nack()



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
                print(f'raw data received:[{data}]')
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
                                # print(f'Processing {c}')
                                if c[0] == ':':
                                    # print(f"Received command: {c}")

                                    response = state_machine.process_command(c)
                                    if response is not None:
                                        print(f'Sending response [{response}]')
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
