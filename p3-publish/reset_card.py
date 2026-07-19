import board
import busio
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI
from Cryptodome.Cipher import DES
import os

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
pn532.SAM_configuration()

APP_ID = [0x01, 0x00, 0x00]
DEFAULT_DES_KEY = bytes(8)

def cmd(c, data=b""):
    payload = [0x01, c] + list(data)
    resp = pn532.call_function(0x40, params=payload, response_length=64)
    if resp is None or len(resp) < 2:
        return None, b""
    return resp[1], bytes(resp[2:])

def rotate_left(b): return b[1:] + b[:1]
def rotate_right(b): return b[-1:] + b[:-1]

def auth_master():
    status, enc_rndB = cmd(0x1A, bytes([0x00]))
    if status != 0xAF or len(enc_rndB) < 8:
        return False
    cipher = DES.new(DEFAULT_DES_KEY, DES.MODE_CBC, iv=bytes(8))
    rndB = cipher.decrypt(enc_rndB[:8])
    rndB_rot = rotate_left(rndB)
    rndA = os.urandom(8)
    cipher = DES.new(DEFAULT_DES_KEY, DES.MODE_CBC, iv=enc_rndB[:8])
    token = cipher.encrypt(rndA + rndB_rot)
    status, enc = cmd(0xAF, token)
    if status != 0x00 or len(enc) < 8:
        return False
    cipher = DES.new(DEFAULT_DES_KEY, DES.MODE_CBC, iv=token[-8:])
    return rotate_right(cipher.decrypt(enc[:8])) == rndA

print("Tap a card to reset (wipe fare app)...")
uid = pn532.read_passive_target(timeout=10)
if uid is None:
    print("No card.")
else:
    print(f"Card UID: {bytes(uid).hex()}")
    # select master app
    cmd(0x5A, bytes([0x00, 0x00, 0x00]))
    print("Authenticating to master key...")
    if auth_master():
        print("  Auth OK")
        status, _ = cmd(0xDA, bytes(APP_ID))   # DeleteApplication
        if status == 0x00:
            print("  >>> Application deleted - card is blank")
        elif status == 0xA0:
            print("  >>> No such application - card already blank")
        else:
            print(f"  DeleteApplication status 0x{status:02x}")
    else:
        print("  Auth failed")
