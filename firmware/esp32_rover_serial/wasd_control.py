"""Drive the ESP32 rover from the keyboard (Windows).

    python wasd_control.py [COM6]

w/a/s/d drive, space or x stops, e/q change speed, t runs the self-test,
Esc quits. Close the Arduino Serial Monitor first - only one program can
hold the COM port.
"""
import sys
import threading
import time
import msvcrt

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
KEYS = set("wasdxqet ")

ser = serial.Serial(PORT, 115200, timeout=0.1)
time.sleep(2.5)                      # opening the port resets the ESP32
ser.reset_input_buffer()
running = True


def heartbeat():
    while running:
        ser.write(b".")
        time.sleep(0.2)


def reader():
    while running:
        line = ser.readline().decode(errors="replace").strip()
        if line:
            print(f"  rover: {line}")


threading.Thread(target=heartbeat, daemon=True).start()
threading.Thread(target=reader, daemon=True).start()

print(f"Connected to {PORT}.  w/a/s/d = drive, space = stop, e/q = speed, t = self-test, Esc = quit")
try:
    while True:
        ch = msvcrt.getwch()
        if ch == "\x1b":
            break
        if ch.lower() in KEYS:
            ser.write(ch.lower().encode())
finally:
    running = False
    time.sleep(0.3)
    ser.write(b" ")                  # always leave the rover stopped
    ser.close()
    print("Stopped and disconnected.")
