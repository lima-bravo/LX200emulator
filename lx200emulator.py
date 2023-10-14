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
import copy


# Emulated telescope state machine

def plog(log):
    current_time = datetime.datetime.now().strftime('%Y-%m-%d@%H:%M:%S')
    print(f'[{current_time}] {log}')

class TelescopeStateMachine:
    menu_structure = {
        'Object' : {
            'Solar System' : {
                'Mercury' : [],
                'Venus' : [],
                'Mars' : [],
                'Jupiter' : [],
                'Saturn' : [],
                'Uranus' : [],
                'Neptune' : [],
                'Pluto' : [],
                'Moon' : ['Overview', 'Landing Sites', 'Craters', 'Mountains', 'Mare,Lakes...', 'Valleys,Rills..' ],
                'Asteroids' : ['Select', 'Add', 'Delete', 'Edit'],
                'Comets' : ['Select', 'Add', 'Delete', 'Edit']
            },
            'Constellation' : {},
            'Deep Sky' : {
                'Named Objects' : [],
                'Galaxies' : [],
                'Nebulas' : [],
                'Planetary Neb.' : [],
                'Star Clusters' : [],
                'Quasars' : [],
                'Black Holes' : [],
                'Abell Clusters' : [],
                'Arp Galaxies' : [],
                'MCG' : [],
                'UGC' : [],
                'Herschel ' : [],
                'IC Objects' : [],
                'NGC Objects' : [],
                'Caldwell Objects' : [],
                'Messier Objects' : [],
                'Custom Catalogs' : []
            },
            'Star' : {
                'Named' : [],
                'Hipparcos Cat.' : [],
                'SAO Catalog' : [],
                'HD Catalog' : [],
                'HR Catalog' : [],
                'Multiple' : [],
                'GCVS(variables)' : [],
                'Nearby' : [],
                'With Planets' : []
            },
            'Satellite' : ['Select', 'Add', 'Delete', 'Edit'],
            'User Object' : ['Select', 'Add', 'Delete', 'Edit'],
            'Landmarks' : ['Select', 'Add', 'Delete', 'Edit'],
            'Identify' : ['Browse', 'Start Search', 'Edit Parameters']
        },
        'Event' : {
            'Sunrise' : [],
            'Sun\'s Transit' : [],
            'Sunset' : [],
            'Moonrise' : [],
            'Moon\'s Transit' : [],
            'Moonset' : [],
            'Moon Phases' : ['Next Full Moon', 'Next New Moon', 'Next 1st Qtr', 'Next 3rd Qtr'],
            'Meteor Showers' : [],
            'Solar Eclipses' : [],
            'Lunar Eclipses' : [],
            'Min. of Algol' : [],
            'Autumn Equinox' : [],
            'Vernal Equinox' : [],
            'Winter Solstice' : [],
            'Summer Solstice' : []
        },
        'Guided Tour' : {},
        'Glossary' : {},
        'Utilities' : {
            'Ambient Temp.' : [],
            'Timer' : ['Set', 'Start/Stop' ],
            'Alarm' : ['Set', 'On/Off'],
            'Eyepiece Calc.' : ['Field of View', 'Magnification', 'Suggest'],
            'Brightest Star' : [],
            'Brightness Adj.' : [],
            'Panel Light' : [],
            'Aux Port Power' : [],
            'Beep' : [],
            'Battery Alarm' : [],
            'Landmark Survey' : [],
            'Sleep Scope' : [],
            'Park Scope' : []
        },
        'Setup' : {
            'Align' : ['Easy', 'One Star', 'Two Star', 'Align on Home', 'Automatic'],
            'Date' : [],
            'Time' : [],
            'Daylight Savings' : [],
            'GPS-UTC Offset' : [],
            'Telescope' : {
                'Mount' : [],
                'Telescope Model' : [],
                'Focal Length' : [],
                'Max Slew Rate' : [],
                'Mnt.Upper Limit' : [],
                'Mnt.Lower Limit' : [],
                'Park Position' : ['Use Current', 'Use Default'],
                'Calibrate Home' : [],
                'Anti-Backlash' : [ 'RA/Az Percent', 'Dec/Alt Percent', 'RA/AZ Train', 'Dec/Alt Train'],
                'Cal. Sensors' : [],
                'Tracking Rate' : [],
                'Guiding Rate' : [],
                'Dec. Guiding' : [],
                'Reverse L/R' : [],
                'Reverse UP/DOWN' : [],
                'Home Sensors' : [],
                'GPS Alignment' : [],
                'RA PEC' : ['On/Off', 'Restore Factory', 'Train', 'Update'],
                'DEC PEC' : ['On/Off', 'Restore Factory', 'Train', 'Update'],
                'Field Derotator' : [],
                'High Precision' : []
            }
        },
        'Targets' : {},
        'Site' : {

        }
    }

    send_buffer = ''

    def __init__(self):
        # RA in hours and DEC in degrees
        self.ra = 18.91
        self.dec = 33.06
        self.mount_mode = 'A'
        self.find_field_diameter = 15
        self.display_line0 = 'Select Item:    '
        self.display_line1 = ' Utilities      '
        self.display = ''
        self.object = '\x97Select Item:     Object         #'
        self.send_buffer = ''  # make the send buffer empty
        self.current_menu_depth = 0
        self.current_menu_keys = [ list(self.menu_structure.keys())[0] ] # get the first entry and make this the default menu entry

    def set_display_line1(self, line):
        self.display_line1 = '{:16}'.format(line)
        plog(f'Set 2nd display line [{self.display_line1}]')


    def navigate_menu(self,command):
        # there are four possible commands:
        # R - enter : select this menu item and access the lower level menu or execute the function
        # L - exit  : leave this menu item and go to a higher leve menu if available
        # U - up : go to a previous menu item
        # D - down : go to a next menu item
        plog(f'{sys._getframe().f_code.co_name} Match {command}, current pointer {self.current_menu_keys}')
        # find the appropriate sub menu structure:
        menu = self.get_level_menu(self.current_menu_keys)
        #
        key = self.current_menu_keys[-1]
        match command:
            case 'U' :
                # go to the previous entry in the current menu structure
                key = self.current_menu_keys[-1] # get the last entry
                key = self.get_previous_menu_entry(menu,key)
                self.current_menu_keys[-1] = key
                plog(f'Moved up to menu entry \'{key}\'')
            case 'D' :
                key = self.current_menu_keys[-1]  # get the last entry
                key = self.get_next_menu_entry(menu, key)
                self.current_menu_keys[-1] = key
                plog(f'Moved down to menu entry \'{key}\'')
            case 'L' :
                if len(self.current_menu_keys)>1:
                    key = self.current_menu_keys.pop() # remove the last entry from the list
                    key = self.current_menu_keys[-1]
                    plog(f'Moved back to menu entry \'{key}\'')
                else:
                    plog(f'NOTE: Already at top level menu')
                    key = self.current_menu_keys[0]
            case 'R' :
                submenu = self.get_sub_menu(self.current_menu_keys)
                plog(f'Submenu [{submenu}]')
                if len(submenu)>0:
                    key = submenu[0]
                    self.current_menu_keys.append(key)
                    plog(f'Moved deeper to \'{key}\'')
                else:
                    key = self.current_menu_keys[-1]
                    plog(f'Executing function {self.current_menu_keys}')
            case _ :
                pass

        self.set_display_line1(key)


    def get_level_menu(self, menu_keys):
        # return a list of peer menu items for the menu item pointed to by the menu_keys
        local = self.menu_structure
        for k in menu_keys:  # iterate down the submenu's
            if type(local) is dict:
                level = list(local.keys())
                local = local[k]
            else:
                level = list(local)

        # plog(f'Peer menu \'{k}\' : {level}')
        return level

    def get_sub_menu(self, menu_keys):
        #
        local = self.menu_structure
        for k in menu_keys: # iterate down the submenu's
            if type(local) is dict:
                local = local[k]
            else:
                local={}


        if type(local) is dict:
            submenu = list(local.keys())
        else:
            submenu = local

        # plog(f'Sub menu \'{k}\' : {submenu}')
        return submenu

    def get_key_index(self, fieldlist, searchkey):
        for i, key in enumerate(fieldlist):
            if key == searchkey:
                index = i
                break

        return index

    def get_previous_menu_entry(self,menu, item):
        if type(menu) is dict:
            fields = list(menu.keys())
        else:
            fields = list(menu)

        index = self.get_key_index(fields,item)

        return fields[index-1] # this will wrap around when index is 0

    def get_next_menu_entry(self,menu, item):
        if type(menu) is dict:
            fields = list(menu.keys())
        else:
            fields = list(menu)

        index = self.get_key_index(fields, item)

        if index == len(fields)-1:
            return fields[0]

        return fields[index + 1]  # this will wrap around when index is 0


    def deg_min_sec(self, degrees):
        deg = int(degrees)
        dmin = 60 * (degrees - deg)
        min = int(dmin)
        dsec = 60 * (dmin - min)
        sec = int(dsec)

        return deg, min, sec

    def nack(self):  # return a 'telescope is busy' response
        return '\x15'

    # +-----------------------------------------------------------------------------------------------------------+
    # function block Telescope Information ':A'
    # +-----------------------------------------------------------------------------------------------------------+

    # +-----------------------------------------------------------------------------------------------------------+
    # function block Display Information ':E'
    # +-----------------------------------------------------------------------------------------------------------+
    def goto(self):
        self.set_display_line1('Slewing')
        return None


    def basic_display(self):
        # return a string that ScopeBoss will recognize
        # start with the string code and close with the double hash
        return f'\x97{self.display_line0}{self.display_line1}#' # no double # needed, this was a misinterpretation

    def get_keypress(self, command):
        target = command[3:]  # get the keypress to the end
        plog(f'{sys._getframe().f_code.co_name} Match {target}')

        match target:
            case '13' : # Enter
                self.navigate_menu('R')
                return None
            case '68' :
                self.navigate_menu('U')
                return None
            case '71' :
                return self.goto()
            case '85' :
                self.navigate_menu('D')
                return None
            case '9': # Mode
                self.navigate_menu('L')
                return None
            case _:
                return self.basic_display()

    def get_handset_display(self, command):
        target = command[1:3]
        # plog(f'{sys._getframe().f_code.co_name} Match {target}')
        match target:
            case 'ED':
                return self.basic_display()  # return the state of the display, the state engine should start covering this
            case 'EK':
                return self.get_keypress(command)
            case _:
                return self.basic_display()

    # +-----------------------------------------------------------------------------------------------------------+
    # function block Telescope Information ':G'
    # +-----------------------------------------------------------------------------------------------------------+

    def get_alignment_menu_entry(self):
        # return 'LX2001#'
        return '#'

    def get_telescope_dec(self):
        deg, min, sec = self.deg_min_sec(self.dec)
        sign = '+'
        if self.dec < 0:
            sign = '-'
        return f'{sign}{deg}*{min}\'{sec}#'

    def get_find_field_diameter(self):
        return '1{:03d}\'#'.format(self.find_field_diameter)

    def get_telescope_ra(self):
        deg, min, sec = self.deg_min_sec(self.ra)
        return f'{deg}:{min}:{sec}#'

    def get_scope_status(self):
        return 'ANP'

    def get_telescope_firmware(self, command):
        target = command[1:4]
        # plog(f'{sys._getframe().f_code.co_name} Match {target}')
        match target:
            case 'GVD':
                return f'{datetime.strftime("%b %d %Y")}#'
            case 'GVN':
                return '4.2l#'
            case 'GVP':
                return 'LX200#'
            case 'GVT':
                return f'{datetime.strftime("%H:%M:%S")}#'

    def process_telescope_information(self, command):
        target = command[1:3]
        # plog(f'{sys._getframe().f_code.co_name} Match {target}')
        match target:
            case 'G0':
                return self.get_alignment_menu_entry()
            case 'GD':
                return self.get_telescope_dec()
            case 'GF':
                return self.get_find_field_diameter()
            case 'GR':
                return self.get_telescope_ra()
            case 'GV':
                return self.get_telescope_firmware(command)
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
        target = command[1:3]
        # plog(f'{sys._getframe().f_code.co_name} Match {target}')
        match target:
            case 'RC':
                return self.set_slew_rate_to_centering()
            case _:
                return self.nack()

    # +-----------------------------------------------------------------------------------------------------------+
    # function block telescope Set commands ':S'
    # +-----------------------------------------------------------------------------------------------------------+
    def set_find_field_diameter(self, command):
        diameter = int(command[3:6])
        plog(f'Field diameter set to {diameter}')
        self.find_field_diameter = diameter
        # if field diameter is valid return 1, else return 0
        # assume that the value is always correct
        return '1'

    def process_telescope_set(self, command):
        target = command[1:3]
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
        # plog(f'{sys._getframe().f_code.co_name} Match {target}')
        result = None
        match target:
            case ':A':
                result = self.process_alignment(command)
            case ':E':
                result = self.get_handset_display(command)
            case ':G':
                result = self.process_telescope_information(command)
            case ':R':
                result = self.process_slew_rate(command)
            case ':S':
                result = self.process_telescope_set(command)
            case _:
                result = self.nack()
        # add the result to the send buffer
        if result is not None:
            self.add_to_send_buffer(result)
            # self.add_to_send_buffer('#') # according to protocol we need to have two ## for closing strings

    def add_to_send_buffer(self, result):
        self.send_buffer += result

    def pop_from_send_buffer(self, length=16):
        # use a maximum length of 16 to pop from the send_buffer
        # let's see if this generates violations...
        result = self.send_buffer[0:length]
        remain = self.send_buffer[length:]

        self.send_buffer = remain

        return result

    def clear_send_buffer(self):
        self.send_buffer = ''

    def has_data(self):
        if len(self.send_buffer) > 0:
            return True

        return False


def listen_for_and_process_data(client_socket, state_machine):
    show_response = True
    while True:
        try:
            data = client_socket.recv(1024)
            # plog(f'raw data received:[{data}]')
            if data == b'':  # empty data received, counter party closed connection
                break  # exit the while loop and close the connection
            else:
                data_buffer = data.decode()  # decode binary string to UTF
                if data_buffer == ':ED#:G0#' :
                    show_response = False
                else:
                    show_response = True
                    plog(f"data received:[{data_buffer}]")

                if '#' in data_buffer:  # the carriage return signals a command,
                    command = data_buffer.split('#')  # split according to the commands

                    for c in command:  # run through all the commands
                        if len(c) > 1:  # make sure it is long enough to hold a command
                            # does it start with ':'
                            if c[0] == ':':
                                state_machine.process_command(c)
        except BlockingIOError as e:
            plog(f'{type(e)}')
            # pass

        while state_machine.has_data():
            # send the data in chunks to emulate the serial line protocol
            # all apps must be able to deal with this
            # check if there is any data waiting to be sent
            response = state_machine.pop_from_send_buffer()
            if show_response:
                plog(f'Sending response [{response}]')
            try:
                client_socket.sendall(response.encode('utf-8'))
            except BrokenPipeError:
                plog("Connection lost")
                break

        time.sleep(0.2)


    # Emulated LX200 telescope server
def emulate_telescope(port):
    state_machine = TelescopeStateMachine()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ip_address = '0.0.0.0'
    server.bind((ip_address, port))
    server.listen()  # removed the number one

    print(f"Emulated LX200 Telescope server is listening on {ip_address}:{port}")

    while True:
        client_socket, client_address = server.accept()
        print(f"Connected to {client_address[0]}:{client_address[1]}")
        # client_socket.setblocking(False)
        listen_for_and_process_data(client_socket, state_machine)

    print(f'Closing socket')
    client_socket.close()


def test_case_pop_send_buffer():
    testrings = [
        '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ',
        'abcdef',
        '1234567890123456'
    ]
    state_machine = TelescopeStateMachine()

    for t in testrings:
        state_machine.clear_send_buffer()
        state_machine.add_to_send_buffer(t)

        while state_machine.has_data():
            result = state_machine.pop_from_send_buffer()
            print(f'Popped from send_buffer : {result}')


def test_menu_structure():
    key_sequences = [
        'LLLLUD',
        'LLLLUUUURUUURUUDDDDDDLDDDDLDDDDD'
    ]
    # start executing the commands against the menu
    state_machine = TelescopeStateMachine()
    target = ['Object']
    menu = state_machine.get_level_menu(target)
    submenu = state_machine.get_sub_menu(target)


    for k in key_sequences:
        next_item = state_machine.get_next_menu_entry(menu,target[0])
        print(f'next item : {next_item}')
        next_item = state_machine.get_previous_menu_entry(menu,target[0])
        print(f'prev item : {next_item}')

        for c in k:
            state_machine.navigate_menu(c)


def run_test_cases():
    # run a set of test cases
    # test_case_pop_send_buffer()
    test_menu_structure()

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python script.py <port>")
        sys.exit(1)

    if sys.argv[1] == 'test':
        run_test_cases()
    else:
        port = int(sys.argv[1])
        emulate_telescope(port)
