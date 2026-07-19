import board, busio, os, struct, zlib
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
def crc_desfire(data):
    return struct.pack("<I", (zlib.crc32(data) & 0xFFFFFFFF) ^ 0xFFFFFFFF)

def auth_master():
    cmd(0x5A, bytes([0x00,0x00,0x00]))
    status, enc = cmd(0x1A, bytes([0x00]))
    if status != 0xAF or len(enc) < 8: return None, None
    enc = enc[:8]
    rndB = DES.new(DEFAULT_DES, DES.MODE_CBC, iv=bytes(8)).decrypt(enc)
    rndA = os.urandom(8)
    token = DES.new(DEFAULT_DES, DES.MODE_CBC, iv=enc).encrypt(rndA + rotl(rndB))
    status, enc2 = cmd(0xAF, token)
    if status != 0x00 or len(enc2) < 8: return None, None
    if rotr(DES.new(DEFAULT_DES, DES.MODE_CBC, iv=token[-8:]).decrypt(enc2[:8])) != rndA:
        return None, None
    return rndA[0:4] + rndB[0:4], token[-8:]

def try_setconfig(variant):
    sk, iv = auth_master()
    if sk is None:
        return "auth failed"
    option = 0x00
    config_byte = 0x02
    if variant == 1:      # CRC over cmd+option+data, IV=running
        crc = crc_desfire(bytes([0x5C, option, config_byte])); use_iv = iv
    elif variant == 2:    # CRC over cmd+option+data, IV=zeros
        crc = crc_desfire(bytes([0x5C, option, config_byte])); use_iv = bytes(8)
    elif variant == 3:    # CRC over just data byte, IV=running
        crc = crc_desfire(bytes([config_byte])); use_iv = iv
    elif variant == 4:    # CRC over just data byte, IV=zeros
        crc = crc_desfire(bytes([config_byte])); use_iv = bytes(8)
    plaintext = bytes([config_byte]) + crc
    while len(plaintext) % 8 != 0:
        plaintext += b"\x00"
    enc_data = DES.new(sk, DES.MODE_CBC, iv=use_iv).encrypt(plaintext)
    status, _ = cmd(0x5C, bytes([option]) + enc_data)
    return f"0x{status:02x}"

print("Tap card 04995972... (will try config variants)")
uid = pn532.read_passive_target(timeout=10)
if uid is None: print("No card."); exit()
print(f"Card UID: {bytes(uid).hex()}")

for v in [2, 4, 1, 3]:
    result = try_setconfig(v)
    print(f"  Variant {v}: {result}")
    if result == "0x00":
        print(f">>> SUCCESS with variant {v} - RANDOM UID ENABLED")
        break
    # need to re-tap between attempts since auth state resets... but try continuing
else:
    print(">>> All variants failed - need deeper investigation")
