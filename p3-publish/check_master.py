import board, busio, os
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI
from Cryptodome.Cipher import DES

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
pn532.SAM_configuration()

DEFAULT_DES = bytes(8)

def cmd(c, data=b""):
    payload = [0x01, c] + list(data)
    resp = pn532.call_function(0x40, params=payload, response_length=64)
    if resp is None or len(resp) < 2: return None, b""
    return resp[1], bytes(resp[2:])
def rotl(b): return b[1:]+b[:1]
def rotr(b): return b[-1:]+b[:-1]

print("Tap card 04995972...")
uid = pn532.read_passive_target(timeout=10)
if uid is None: print("No card."); exit()
print(f"Card UID: {bytes(uid).hex()}")

# select PICC master (AID 000000)
cmd(0x5A, bytes([0x00,0x00,0x00]))
# DES auth with default key
status, enc = cmd(0x1A, bytes([0x00]))
if status != 0xAF or len(enc) < 8:
    print(f"  Master auth start failed: 0x{status:02x}"); exit()
enc = enc[:8]
rndB = DES.new(DEFAULT_DES, DES.MODE_CBC, iv=bytes(8)).decrypt(enc)
rndA = os.urandom(8)
token = DES.new(DEFAULT_DES, DES.MODE_CBC, iv=enc).encrypt(rndA + rotl(rndB))
status, enc2 = cmd(0xAF, token)
if status != 0x00 or len(enc2) < 8:
    print(f"  Master auth part2 failed: 0x{status:02x}"); exit()
rndA_card = rotr(DES.new(DEFAULT_DES, DES.MODE_CBC, iv=token[-8:]).decrypt(enc2[:8]))
if rndA_card == rndA:
    # derive DES session key: RndA[0:4] + RndB[0:4]
    sk = rndA[0:4] + rndB[0:4]
    print(f"  [+] MASTER AUTH OK - DES session key: {sk.hex()}")
    print("  Ready for SetConfiguration.")
else:
    print("  RndA mismatch")
