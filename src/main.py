# main.py — ESP32-C3 Power Control
# Controls two output pins (5V, VBAT) via button, UART, WiFi and Bluetooth.

import bluetooth
import machine
import network
import select
import socket
import sys
import time

from boot import load_config


# Configuration
CONFIG = load_config()

# GPIO / Button
PIN_BUTTON = CONFIG.get('PIN_BUTTON', 9)
PIN_5V = CONFIG.get('PIN_5V', 10)
PIN_VBAT = CONFIG.get('PIN_VBAT', 3)
LONG_PRESS_MS = 800     # Threshold to distinguish short/long press
pin_button = machine.Pin(PIN_BUTTON, machine.Pin.IN, machine.Pin.PULL_UP)
pin_5v = machine.Pin(PIN_5V, machine.Pin.OUT, value=0)
pin_vbat = machine.Pin(PIN_VBAT, machine.Pin.OUT, value=0)

# Button state
button_pressed = False
button_press_start = 0
long_press_triggered = False

# UART
uart_buffer = ''

# WiFi
WIFI_PORT = CONFIG.get('ESP32_PORT', 8080)
wifi_server = None

# BLE
BLE_DEVICE_NAME = CONFIG.get('ESP32_BLE_DEVICE_NAME', 'PowerCtrl')
BLE_SERVICE_UUID = bluetooth.UUID(CONFIG.get('ESP32_BLE_SERVICE_UUID', ''))
BLE_CHAR_UUID = bluetooth.UUID(CONFIG.get('ESP32_BLE_CHAR_UUID', ''))
ble = bluetooth.BLE()
ble_conn_handle = None
ble_char_handle = None

# Shared state
state = {
    '5V': False,
    'VBAT': False,
}


def set_pin(channel, on):
    '''Set output pin high or low and update state.'''
    if channel == '5V':
        pin_5v.value(1 if on else 0)
        state['5V'] = on
    elif channel == 'VBAT':
        pin_vbat.value(1 if on else 0)
        state['VBAT'] = on
    status = 'ON' if on else 'OFF'
    print('[Pin] {} set {}'.format(channel, status))
    return '{} set {}'.format(channel, status)


def toggle_pin(channel):
    '''Toggle the given channel.'''
    return set_pin(channel, not state[channel])


def parse_command(raw):
    '''
    Parse a command string.
    Supported commands:
        5V ON / 5V OFF
        VBAT ON / VBAT OFF
        STATUS
    Returns response string.
    '''
    cmd = raw.strip().upper()

    if cmd == '5V ON':
        return set_pin('5V', True)
    elif cmd == '5V OFF':
        return set_pin('5V', False)
    elif cmd == 'VBAT ON':
        return set_pin('VBAT', True)
    elif cmd == 'VBAT OFF':
        return set_pin('VBAT', False)
    elif cmd == 'STATUS':
        v5_status = 'ON' if state['5V'] else 'OFF'
        vbat_status = 'ON' if state['VBAT'] else 'OFF'
        return '5V is {}, VBAT is {}'.format(v5_status, vbat_status)
    else:
        return 'ERR: Unknown command "{}"'.format(raw.strip())


def poll_button():
    '''
    Non-blocking button polling with immediate long-press detection.
    Returns True if a press was handled.
    Boot button is active-low (pressed = 0).
    '''
    global button_pressed, button_press_start, long_press_triggered
    
    is_pressed = pin_button.value() == 0
    
    if is_pressed:
        if not button_pressed:
            # Button just pressed
            button_pressed = True
            button_press_start = time.ticks_ms()
            long_press_triggered = False
        else:
            # Button is still held, check for long press threshold
            press_duration = time.ticks_diff(time.ticks_ms(), button_press_start)
            if not long_press_triggered and press_duration >= LONG_PRESS_MS:
                # Long press threshold exceeded - trigger immediately
                print('[Button] Long press — toggle VBAT')
                toggle_pin('VBAT')
                long_press_triggered = True
    else:
        # Button is released
        if button_pressed:
            button_pressed = False
            if not long_press_triggered:
                # Short press (threshold was not exceeded)
                print('[Button] Short press — toggle 5V')
                toggle_pin('5V')
            long_press_triggered = False
            return True
    
    return False


def poll_uart():
    '''Read available bytes from UART/REPL and process complete lines.'''
    global uart_buffer
    while True:
        select_result = select.select([sys.stdin], [], [], 0)
        if not (select_result and sys.stdin in select_result[0]):
            break
        char = sys.stdin.read(1)
        if char in ('\n', '\r'):
            if uart_buffer:
                response = parse_command(uart_buffer)
                print(response)
                uart_buffer = ''
        else:
            uart_buffer += char


def start_wifi_server():
    '''Start a non-blocking TCP server if WiFi is connected.'''
    global wifi_server
    station = network.WLAN(network.STA_IF)
    if not station.isconnected():
        print('[WiFi] Not connected — TCP server not started')
        return

    ip_address = station.ifconfig()[0]
    wifi_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    wifi_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    wifi_server.bind(('0.0.0.0', WIFI_PORT))
    wifi_server.listen(1)
    wifi_server.setblocking(False)
    print('[WiFi] TCP server listening on {}:{}'.format(ip_address, WIFI_PORT))


def poll_wifi():
    '''Accept connection, read command, respond, close.'''
    if wifi_server is None:
        return
    try:
        client, addr = wifi_server.accept()
    except OSError:
        return

    try:
        client.settimeout(2.0)
        data = client.recv(256)
        if data:
            command = data.decode('utf-8').strip()
            print('[WiFi] Command from {}: {}'.format(addr[0], command))
            response = parse_command(command)
            client.send((response + '\n').encode('utf-8'))
    except Exception as e:
        print('[WiFi] Error: {}'.format(e))
    finally:
        client.close()


def ble_irq(event, data):
    '''BLE interrupt handler.'''
    global ble_conn_handle

    # Central connected
    if event == 1:
        ble_conn_handle = data[0]
        print('[BLE] Device connected')

    # Central disconnected — restart advertising
    elif event == 2:
        ble_conn_handle = None
        print('[BLE] Device disconnected')
        ble_start_advertising()

    # Write from client
    elif event == 3:
        conn, attr = data
        raw = ble.gatts_read(attr)
        if raw:
            command = raw.decode('utf-8').strip()
            print('[BLE] Command: {}'.format(command))
            response = parse_command(command)
            if ble_char_handle is not None:
                ble.gatts_write(ble_char_handle, 
                                (response + '\n').encode('utf-8'))
                if ble_conn_handle is not None:
                    ble.gatts_notify(ble_conn_handle, ble_char_handle)


def ble_start_advertising():
    '''Start BLE advertising with device name.'''
    name = BLE_DEVICE_NAME.encode('utf-8')
    # Build advertising payload: flags + complete name
    adv_data = bytearray(b'\x02\x01\x06')
    adv_data += bytearray([len(name) + 1, 0x09]) + name
    ble.gap_advertise(100_000, adv_data)
    print('[BLE] Advertising as "{}"'.format(BLE_DEVICE_NAME))


def start_ble():
    '''Initialize BLE GATT server.'''
    global ble_char_handle
    ble.active(True)
    ble.irq(ble_irq)

    service = (
        BLE_SERVICE_UUID,
        (
            (BLE_CHAR_UUID, 
            bluetooth.FLAG_READ | bluetooth.FLAG_WRITE | bluetooth.FLAG_NOTIFY),
        ),
    )
    ((ble_char_handle,),) = ble.gatts_register_services((service,))

    ble_start_advertising()
    print('[BLE] Service started')


def main():
    print('=' * 40)
    print('  ESP32-C3 Power Control')
    print('  5V pin:   GPIO{}'.format(PIN_5V))
    print('  VBAT pin: GPIO{}'.format(PIN_VBAT))
    print('  Button:   GPIO{}'.format(PIN_BUTTON))
    print('=' * 40)

    start_wifi_server()
    start_ble()

    print('[Ready] Waiting for commands...')
    print('  Short press BOOT -> toggle 5V')
    print('  Long  press BOOT -> toggle VBAT')
    print('  UART/WiFi/BLE: "5V ON/OFF", "VBAT ON/OFF", "STATUS"')

    while True:
        poll_button()
        poll_uart()
        poll_wifi()
        time.sleep_ms(20)


if __name__ == '__main__':
    main()
