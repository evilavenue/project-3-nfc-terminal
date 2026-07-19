import sys, board, busio, os
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI
from Cryptodome.Cipher import AES

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
pn532.SAM_configuration()

COCO_APP = [0xC0, 0xC0, 0xC0]
FARE_APP = [0x01, 0x00, 0x00]
ID_FILE = 0x02          # card-ID data file (separate from value file 0x01)
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

def identify_app():
    if cmd(0x5A, bytes(COCO_APP))[0] == 0x00: return "coco", COCO_APP
    if cmd(0x5A, bytes(FARE_APP))[0] == 0x00: return "fare", FARE_APP
    return "unknown", None

if len(sys.argv) < 2:
    print("Usage: python3 assign_id.py <card_id>  (e.g. 1001)")
    sys.exit(1)
card_id = int(sys.argv[1])

print(f"Tap a card to assign ID {card_id}...")
uid = pn532.read_passive_target(timeout=10)
if uid is None: print("No card."); exit()
print(f"Card UID (raw): {bytes(uid).hex()}")

kind, app = identify_app()
print(f"App type: {kind}")
if app is None: print("Unknown card."); exit()

# authenticate with Evil Avenue key
cmd(0x5A, bytes(app))
if not aes_auth(EVIL_KEY):
    print("Auth with Evil Avenue key failed."); exit()
print("Authenticated with Evil Avenue key.")

# create the ID file (standard data file, 4 bytes, plain comm, free access)
# CreateStdDataFile (0xCD): fileNo, commMode, accessRights(2), size(3 LE)
access = [0xEE, 0xEE]
size = list((4).to_bytes(3, "little"))
cmd(0xCD, bytes([ID_FILE, 0x00] + access + size))  # ok if already exists

# write the card ID (WriteData 0x3D): fileNo, offset(3), length(3), data
id_bytes = card_id.to_bytes(4, "little")
offset = list((0).to_bytes(3, "little"))
length = list((4).to_bytes(3, "little"))
status, _ = cmd(0x3D, bytes([ID_FILE] + offset + length) + id_bytes)
if status == 0x00:
    # read it back to confirm
    roffset = list((0).to_bytes(3, "little")); rlen = list((4).to_bytes(3, "little"))
    status, out = cmd(0xBD, bytes([ID_FILE] + roffset + rlen))
    if status == 0x00 and len(out) >= 4:
        got = int.from_bytes(out[:4], "little")
        print(f">>> Card ID written and verified: {got}")
    else:
        print(f">>> Written (status 0x{status:02x}) but readback failed")
else:
    print(f">>> WriteData failed: 0x{status:02x}")
