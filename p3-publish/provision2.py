import sys
import board
import busio
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
print(f"PN532 online")
pn532.SAM_configuration()

COCO_APP = [0xC0, 0xC0, 0xC0]     # Coco Bank credit card app
FARE_APP = [0x01, 0x00, 0x00]     # fare card app
FILE_ID = 0x01
AES_KEYS = 0x81                    # 0x80 = AES, 1 key
KEY_SETTINGS = 0x0F

def cmd(c, data=b"", label=""):
    payload = [0x01, c] + list(data)
    resp = pn532.call_function(0x40, params=payload, response_length=64)
    if resp is None or len(resp) < 2:
        print(f"  {label}: no/short response")
        return None, b""
    status = resp[1]
    out = bytes(resp[2:])
    print(f"  {label}: status 0x{status:02x}" + (f" data {out.hex()}" if out else ""))
    return status, out

def le_bytes(n, size):
    return list(n.to_bytes(size, "little", signed=True))

def provision_coco():
    print("\nProvisioning COCO BANK credit card...")
    cmd(0x5A, bytes([0x00, 0x00, 0x00]), "SelectApp(master)")
    data = COCO_APP + [KEY_SETTINGS, AES_KEYS]
    cmd(0xCA, bytes(data), "CreateApplication(Coco Bank)")
    cmd(0x5A, bytes(COCO_APP), "SelectApp(Coco Bank)")
    print("  >>> Coco Bank card ready (authorizer, no balance)")

def provision_fare(balance):
    print(f"\nProvisioning FARE card with ${balance/100:.2f}...")
    cmd(0x5A, bytes([0x00, 0x00, 0x00]), "SelectApp(master)")
    data = FARE_APP + [KEY_SETTINGS, AES_KEYS]
    cmd(0xCA, bytes(data), "CreateApplication(fare)")
    cmd(0x5A, bytes(FARE_APP), "SelectApp(fare)")
    lower = le_bytes(0, 4)
    upper = le_bytes(100000, 4)
    value = le_bytes(balance, 4)
    access = [0xEE, 0xEE]
    vdata = [FILE_ID, 0x00] + access + lower + upper + value + [0x00]
    cmd(0xCC, bytes(vdata), "CreateValueFile")
    status, out = cmd(0x6C, bytes([FILE_ID]), "GetValue")
    if status == 0x00 and len(out) >= 4:
        bal = int.from_bytes(out[:4], "little", signed=True)
        print(f"  >>> Fare card balance: ${bal/100:.2f}")

if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else "coco"
    print(f"Tap card to provision as: {role}")
    uid = pn532.read_passive_target(timeout=10)
    if uid is None:
        print("No card.")
        sys.exit(1)
    print(f"Card UID: {bytes(uid).hex()}")
    if role == "coco":
        provision_coco()
    else:
        provision_fare(int(role))
