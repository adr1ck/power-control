'''
power_control_cli.py — Host-side CLI for ESP32-C3 Power Control.

Send commands (5V ON/OFF, VBAT ON/OFF, STATUS) over:
  - COM (serial/UART)
  - WiFi (TCP socket)
  - Auto-detect (tries COM → WiFi → BLE)

Configuration via config.json:
  - ESP32_SERIAL_PORT: Default serial port
  - ESP32_IP: Default WiFi IP address
  - ESP32_PORT: Default WiFi TCP port (default: 8080)

Usage:
    python power_control_cli.py --mode com --port COM3 "5V ON"
    python power_control_cli.py --mode wifi --host 192.168.1.111 "VBAT OFF"
    python power_control_cli.py "5V ON"           # auto-detect
    python power_control_cli.py --interactive     # interactive REPL
'''

import argparse
import json
import os
import socket
import sys
import time

import serial
from bleak import BleakClient, BleakScanner
from serial.tools import list_ports


def load_config():
    '''Load configuration from config.json file.'''
    config = {}
    config_path = 'config.json'
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as file:
                config = json.load(file)
            return config
        except Exception as e:
            print('Warning: Could not load config.json: {}'.format(e))
    return config


CONFIG = load_config()

WIFI_PORT = CONFIG.get('ESP32_PORT', 8080) 


def send_via_com(command, port=None, baudrate=115200, timeout=2, ser=None):
    '''Send command over serial port and return response.
    If `ser` is provided, uses that existing connection instead of opening a new one.'''
    own_conn = ser is None
    if own_conn:
        # Create serial object without opening yet to set DTR/RTS before connection
        ser = serial.Serial(port=None, baudrate=baudrate, timeout=timeout)
        ser.port = port
        ser.dtr = False
        ser.rts = False
        ser.open()
        time.sleep(0.2)

    try:
        # Flush any pending data
        ser.reset_input_buffer()

        # Send command followed by newline
        ser.write((command + '\r\n').encode('utf-8'))
        time.sleep(0.3)

        # Read response lines
        response_lines = []
        while ser.in_waiting or not response_lines:
            line = ser.readline().decode('utf-8', errors='replace').strip()
            if line:
                response_lines.append(line)
            if not ser.in_waiting:
                break

        return '\n'.join(response_lines) if response_lines else '(no response)'
    finally:
        # Don't close in single-command mode to avoid port close glitches
        if not own_conn:
            ser.close()


def find_serial_port():
    '''Auto-detect the first available serial port.'''
    ports = list(list_ports.comports())
    for port_info in ports:
        description = (port_info.description or '').lower()
        vid = port_info.vid
        # ESP32-C3 common USB VID (Espressif)
        if vid == 0x303A or 'cp210' in description or 'ch340' in description or 'usb' in description:
            return port_info.device
    # Fallback: return first port if any
    if ports:
        return ports[0].device
    return None


def send_via_wifi(command, host, port=WIFI_PORT, timeout=3):
    '''Send command over TCP and return response.'''
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall((command + '\n').encode('utf-8'))
        data = sock.recv(256)
        return data.decode('utf-8').strip()
    

def detect_auto_transport(host=None, port=None, baudrate=115200):
    '''Probe transports with STATUS command, return send_fn for the first working one.'''

    # 1. Try COM
    serial_port = find_serial_port()
    if serial_port:
        try:
            print('[Auto] Trying COM ({})...'.format(serial_port))
            resp = send_via_com('STATUS', serial_port, baudrate)
            print('[Auto] COM connected: {}'.format(resp))
            return lambda cmd: send_via_com(cmd, serial_port, baudrate)
        except Exception as e:
            print('[Auto] COM failed: {}'.format(e))

    # 2. Try WiFi
    if host:
        try:
            wifi_port = port or WIFI_PORT
            print('[Auto] Trying WiFi ({}:{})...'.format(host, wifi_port))
            resp = send_via_wifi('STATUS', host, wifi_port)
            print('[Auto] WiFi connected: {}'.format(resp))
            return lambda cmd: send_via_wifi(cmd, host, wifi_port)
        except Exception as e:
            print('[Auto] WiFi failed: {}'.format(e))

    return None


def interactive_loop(send_fn):
    '''REPL-style interactive command loop.'''
    print('Interactive mode. Type commands or "quit" to exit.')
    print('Commands: 5V ON, 5V OFF, VBAT ON, VBAT OFF, STATUS')
    print()

    while True:
        try:
            cmd = input('power> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd:
            continue
        if cmd.lower() in ('quit', 'exit', 'q'):
            break

        try:
            response = send_fn(cmd)
            print(response)
        except Exception as e:
            print('Error: {}'.format(e))


def build_parser():
    # Get defaults from config.json
    default_host = CONFIG.get('ESP32_IP')
    default_port = int(WIFI_PORT)
    default_serial_port = CONFIG.get('ESP32_SERIAL_PORT')

    parser = argparse.ArgumentParser(
        description='ESP32-C3 Power Control CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python power_control_cli.py "5V ON"\n'
            '  python power_control_cli.py --mode com --port COM3 "VBAT OFF"\n'
            '  python power_control_cli.py --mode wifi --host 192.168.1.111 "STATUS"\n'
            '  python power_control_cli.py --interactive\n'
        ),
    )
    parser.add_argument(
        'command',
        nargs='?',
        help='Command to send (e.g. "5V ON", "VBAT OFF", "STATUS")',
    )
    parser.add_argument(
        '--mode', '-m',
        choices=['auto', 'com', 'wifi'],
        default='auto',
        help='Transport mode (default: auto)',
    )
    parser.add_argument(
        '--port', '-p',
        default=default_serial_port,
        help='Serial port (e.g. COM3, /dev/ttyUSB0, default from config.json: {})'.format(
            default_serial_port or 'auto-detect'
        ),
    )
    parser.add_argument(
        '--host', '-H',
        default=default_host,
        help='WiFi IP address of ESP32-C3 (default from config.json: {})'.format(
            default_host or 'not set'
        ),
    )
    parser.add_argument(
        '--wifi-port',
        type=int,
        default=default_port,
        help='WiFi TCP port (default from config.json or {})'.format(WIFI_PORT),
    )
    parser.add_argument('--baudrate', '-b', type=int, default=115200, help='Serial baudrate')
    parser.add_argument(
        '--interactive', '-i', action='store_true',
        help='Interactive command mode',
    )
    return parser


def make_send_fn(args):
    '''Build a send function based on selected mode and arguments.'''
    mode = args.mode

    if mode == 'com':
        port = args.port or find_serial_port()
        if not port:
            print('Error: No serial port found. Specify with --port.')
            sys.exit(1)
        return lambda cmd: send_via_com(cmd, port, args.baudrate)

    elif mode == 'wifi':
        if not args.host:
            print('Error: --host is required for WiFi mode (or set ESP32_IP in config.json).')
            sys.exit(1)
        return lambda cmd: send_via_wifi(cmd, args.host, args.wifi_port)

    else:  # auto
        send_fn = detect_auto_transport(
            host=args.host, port=args.wifi_port, baudrate=args.baudrate
        )
        if send_fn is None:
            print('Error: Could not connect via any transport.')
            sys.exit(1)
        return send_fn


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command and not args.interactive:
        parser.print_help()
        sys.exit(0)

    send_fn = make_send_fn(args)

    if args.interactive:
        interactive_loop(send_fn)
    else:
        try:
            response = send_fn(args.command)
            print(response)
        except Exception as e:
            print('Error: {}'.format(e))
            sys.exit(1)


if __name__ == '__main__':
    main()
