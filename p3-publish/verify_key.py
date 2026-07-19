import board, busio, os
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI
from Cryptodome.Cipher import AES

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
pn532.SAM_configuration()

FARE_APP = [0x01, 0x00, 0x00]
DEFAULT = bytes(16)
EVIL_KEY = bytes.fromhex("REPLACE_WITH_YOUR_OWN_AES128_KEY")

def cmd(c, data=b""):
    payload = [0x01, c] + list(data)
    resp = pn532.call_function(0x40, params=payload, response_length=64)
    if resp is None or len(resp) < 2: return None, b""
    return resp[1], bytes(resp[2:])
def rotl(b): return b[1:]+b[:1]
def rotr(b): return b[-1:]+b[:-1]

def try_auth(key, label):
    cmd(0x5A, bytes(FARE_APP))
    status, enc = cmd(0xAA, bytes([0]))
    if status != 0xAF or len(enc) < 16:
        print(f"  {label}: auth start failed (0x{status:02x})")
        return False
    enc = enc[:16]
    rndB = AES.new(key, AES.MODE_CBC, iv=bytes(16)).decrypt(enc)
    rndA = os.urandom(16)
    token = AES.new(key, AES.MODE_CBC, iv=enc).encrypt(rndA + rotl(rndB))
    status, enc2 = cmd(0xAF, token)
    if status != 0x00 or len(enc2) < 16:
        print(f"  {label}: REJECTED (0x{status:02x})")
        return False
    rndA_card = rotr(AES.new(key, AES.MODE_CBC, iv=token[-16:]).decrypt(enc2[:16]))
    if rndA_card == rndA:
        print(f"  {label}: AUTH OK")
        return True
    print(f"  {label}: RndA mismatch")
    return False

print("Tap the owned card (04a12b72...)...")
uid = pn532.read_passive_target(timeout=10)
if uid is None: print("No card."); exit()
print(f"Card UID: {bytes(uid).hex()}")
print("Testing OLD default key (should FAIL):")
try_auth(DEFAULT, "default key")
print("Testing NEW Evil Avenue key (should SUCCEED):")
try_auth(EVIL_KEY, "evil key")
