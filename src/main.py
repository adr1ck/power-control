# main.py — ESP32-C3 Power Control
# Controls two output pins (5V, VBAT) via button, UART.

import machine
import select
import sys
import time
import json


def load_config():
    '''Load configuration from config.json file.'''
    config = {}
    try:
        with open('config.json', 'r') as file:
            config = json.load(file)
        return config
    except OSError:
        print('[Config] config.json file not found')
        return {}


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


def main():
    print('=' * 40)
    print('  ESP32-C3 Power Control')
    print('  5V pin:   GPIO{}'.format(PIN_5V))
    print('  VBAT pin: GPIO{}'.format(PIN_VBAT))
    print('  Button:   GPIO{}'.format(PIN_BUTTON))    
    print('=' * 40)

    print('[Ready] Waiting for commands...')
    print('  Short press BOOT -> toggle 5V')
    print('  Long  press BOOT -> toggle VBAT')
    print('  UART: "5V ON/OFF", "VBAT ON/OFF", "STATUS"')

    while True:
        poll_button()
        poll_uart()
        time.sleep_ms(20)


if __name__ == '__main__':
    main()
