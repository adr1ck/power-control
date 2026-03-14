# boot.py — Runs on ESP32-C3 boot before main.py
# Connects to WiFi using credentials from config.json

import network
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
        print('[WiFi] config.json file not found')
        return {}


def connect_wifi():
    '''Try connecting to WiFi networks listed in config.json, optimized by signal strength.'''
    config = load_config()
    networks = config.get('WIFI', [])
    
    if not networks:
        print('[WiFi] No WiFi credentials in config.json')
        print('[WiFi] Copy config.json.example -> config.json and add credentials')
        return False

    station = network.WLAN(network.STA_IF)
    station.active(True)

    # Create a dictionary of SSID -> password from config
    config_networks = {entry.get('SSID'): entry.get('PASSWORD', '') for entry in networks}
    
    # Scan available networks
    print('[WiFi] Scanning available networks...')
    available_networks = station.scan()
    
    # Filter and sort: keep only networks from config, sorted by signal strength (RSSI)
    # Scan returns: (ssid, bssid, channel, rssi, auth_type, hidden)
    prioritized = []
    for scan_result in available_networks:
        ssid = scan_result[0].decode() if isinstance(scan_result[0], bytes) else scan_result[0]
        rssi = scan_result[3]
        
        if ssid in config_networks:
            prioritized.append((ssid, rssi))
            print('[WiFi] Found "{}" (signal: {} dBm)'.format(ssid, rssi))
    
    # Sort by signal strength descending (higher RSSI = stronger signal)
    prioritized.sort(key=lambda x: x[1], reverse=True)
    
    if not prioritized:
        print('[WiFi] No configured networks found in scan results')
        station.active(False)
        return False
    
    print('[WiFi] Attempting to connect in order of signal strength...')
    
    # Try connecting to networks in order of signal strength
    for ssid, rssi in prioritized:
        password = config_networks[ssid]
        
        print('[WiFi] Connecting to "{}" (signal: {} dBm)...'.format(ssid, rssi))
        station.connect(ssid, password)

        # Wait up to 10 seconds for connection
        for attempt in range(20):
            if station.isconnected():
                static_config = (
                    config.get('ESP32_IP', '192.168.1.111'),
                    config.get('ESP32_SUBNET', '255.255.255.0'),
                    config.get('ESP32_GATEWAY', '192.168.1.1'),
                    config.get('ESP32_DNS', '8.8.8.8'),
                )
                station.ifconfig(static_config)
                
                ip_address = station.ifconfig()[0]
                print('[WiFi] Connected! Static IP: {}'.format(ip_address))
                return True
            time.sleep(0.5)

        print('[WiFi] Failed to connect to "{}"'.format(ssid))
        station.disconnect()

    print('[WiFi] Could not connect to any network')
    station.active(False)
    return False


if __name__ == '__main__':
    connect_wifi()
