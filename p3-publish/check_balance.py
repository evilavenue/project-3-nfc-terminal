import board, busio, os
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI
from Cryptodome.Cipher import AES

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
pn532.SAM_configuration()

COCO_APP = [0xC0, 0xC0, 0xC0]
FARE_APP = [0x01, 0x00, 0x00]
FILE_ID = 0x01
ID_FILE = 0x02
EVIL_KEY = bytes.fromhex("REPLACE_WITH_YOUR_OWN_AES128_KEY")

def cmd(c, data=b""):
    payload = [0x01, c] + list(data)
    resp = pn532.call_function(0x40, params=payload, response_length=64)
    if resp is None or len(resp) < 2: return None, b""
    return resp[1], bytes(resp[2:])
def rotl(b): return b[1:]+b[:1]
def rotr(b): return b[-1:]+b[:-1]

def aes_auth(key, key_no=0):
    status, enc = cmd(0xAA, bytes([key_no]))
    if status != 0xAF or len(enc) < 16: return False
    enc = enc[:16]
    rndB = AES.new(key, AES.MODE_CBC, iv=bytes(16)).decrypt(enc)
    rndA = os.urandom(16)
    token = AES.new(key, AES.MODE_CBC, iv=enc).encrypt(rndA + rotl(rndB))
    status, enc2 = cmd(0xAF, token)
    if status != 0x00 or len(enc2) < 16: return False
    return rotr(AES.new(key, AES.MODE_CBC, iv=token[-16:]).decrypt(enc2[:16])) == rndA

def identify():
    if cmd(0x5A, bytes(COCO_APP))[0] == 0x00: return "coco", COCO_APP
    if cmd(0x5A, bytes(FARE_APP))[0] == 0x00: return "fare", FARE_APP
    return "unknown", None

def read_card_id():
    offset = list((0).to_bytes(3, "little"))
    length = list((4).to_bytes(3, "little"))
    status, out = cmd(0xBD, bytes([ID_FILE] + offset + length))
    if status == 0x00 and len(out) >= 4:
        return int.from_bytes(out[:4], "little")
    return None

print("Tap a card to check...")
uid = pn532.read_passive_target(timeout=10)
if uid is None:
    print("No card."); exit()
uid_hex = bytes(uid).hex()
tag = "  [RANDOM UID]" if uid_hex.startswith("08") else ""
print(f"UID: {uid_hex}{tag}")

kind, app = identify()
print(f"App: {kind}")
if app is None:
    print(">>> Not an Evil Avenue card."); exit()

cmd(0x5A, bytes(app))
if not aes_auth(EVIL_KEY):
    print(">>> AUTH FAILED - not authorized with the Evil Avenue key"); exit()
print("Auth OK (Evil Avenue key)")

cid = read_card_id()
print(f"Internal card ID: {cid if cid is not None else 'none'}")

if kind == "fare":
    status, out = cmd(0x6C, bytes([FILE_ID]))
    if status == 0x00 and len(out) >= 4:
        bal = int.from_bytes(out[:4], "little", signed=True)
        print(f">>> Balance: {bal} cents  (${bal/100:.2f})")
    else:
        print(f">>> Balance read failed (0x{status:02x})")
else:
    print(">>> Coco Bank card (authorizer — no balance)")
