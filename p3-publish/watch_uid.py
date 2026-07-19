import board, busio, time
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
pn532.SAM_configuration()

print("Tap the SAME card repeatedly - watch the UID change.")
print("(Ctrl+C to stop)\n")
last = None
while True:
    uid = pn532.read_passive_target(timeout=0.5)
    if uid is not None:
        h = bytes(uid).hex()
        if h != last:
            first = "RANDOM (0x08)" if h.startswith("08") else "static"
            print(f"  UID: {h}   [{first}]")
            last = h
    time.sleep(0.3)
