import board, busio, os, struct
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI
from Cryptodome.Cipher import AES
import zlib

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
pn532.SAM_configuration()

FARE_APP = [0x01, 0x00, 0x00]
DEFAULT_AES_KEY = bytes(16)
# our new owned key - Evil Avenue fare key
NEW_KEY = bytes.fromhex("REPLACE_WITH_YOUR_OWN_AES128_KEY")  # "Evi!AvefrareP3K!" ish
KEY_VERSION = 0x01

def cmd(c, data=b""):
    payload = [0x01, c] + list(data)
    resp = pn532.call_function(0x40, params=payload, response_length=64)
    if resp is None or len(resp) < 2:
        return None, b""
    return resp[1], bytes(resp[2:])

def rotl(b): return b[1:] + b[:1]
def rotr(b): return b[-1:] + b[:-1]

def aes_auth(key, key_no=0):
    status, enc_rndB = cmd(0xAA, bytes([key_no]))
    if status != 0xAF or len(enc_rndB) < 16:
        return None, None
    enc_rndB = enc_rndB[:16]
    rndB = AES.new(key, AES.MODE_CBC, iv=bytes(16)).decrypt(enc_rndB)
    rndA = os.urandom(16)
    token = AES.new(key, AES.MODE_CBC, iv=enc_rndB).encrypt(rndA + rotl(rndB))
    status, enc = cmd(0xAF, token)
    if status != 0x00 or len(enc) < 16:
        return None, None
    rndA_card = rotr(AES.new(key, AES.MODE_CBC, iv=token[-16:]).decrypt(enc[:16]))
    if rndA_card != rndA:
        return None, None
    session_key = rndA[0:4] + rndB[0:4] + rndA[12:16] + rndB[12:16]
    # the running IV after auth = last block of the last thing we sent (token)
    last_iv = enc[:16]  # some impls use enc'd response; we'll try zero IV first
    return session_key, last_iv

def crc32_desfire(data):
    # DESFire CRC32: standard CRC32 but WITHOUT the final XOR-out inversion.
    # zlib.crc32 applies final XOR 0xFFFFFFFF, so we invert it back.
    crc = zlib.crc32(data) & 0xFFFFFFFF
    crc = crc ^ 0xFFFFFFFF   # undo zlib's final inversion
    return struct.pack("<I", crc)

def change_key(session_key, key_no, new_key, version):
    # Build: newKey (16) + version (1)
    key_data = new_key + bytes([version])
    # CRC over: cmd(0xC4) + keyNo + newKey + version
    crc_input = bytes([0xC4, key_no]) + new_key + bytes([version])
    crc = crc32_desfire(crc_input)
    print(f"  CRC input: {crc_input.hex()}")
    print(f"  CRC32:     {crc.hex()}")
    # plaintext = newKey + version + CRC, padded to 16-byte boundary with zeros
    plaintext = new_key + bytes([version]) + crc
    while len(plaintext) % 16 != 0:
        plaintext += b"\x00"
    print(f"  Plaintext: {plaintext.hex()}")
    # encrypt with session key, IV = zeros (first op after auth)
    enc = AES.new(session_key, AES.MODE_CBC, iv=bytes(16)).encrypt(plaintext)
    print(f"  Encrypted: {enc.hex()}")
    # send 0xC4 + keyNo + encrypted
    status, resp = cmd(0xC4, bytes([key_no]) + enc)
    print(f"  ChangeKey status: 0x{status:02x}")
    return status == 0x00

print("Tap the test fare card (04a12b72...)...")
uid = pn532.read_passive_target(timeout=10)
if uid is None:
    print("No card."); exit()
print(f"Card UID: {bytes(uid).hex()}")
cmd(0x5A, bytes(FARE_APP))
print("AES auth with default key...")
sk, iv = aes_auth(DEFAULT_AES_KEY)
if sk is None:
    print("Auth failed."); exit()
print(f"  Session key: {sk.hex()}")
print("Attempting ChangeKey (key 0: default -> Evil Avenue key)...")
if change_key(sk, 0, NEW_KEY, KEY_VERSION):
    print(">>> KEY CHANGED SUCCESSFULLY")
else:
    print(">>> ChangeKey failed (0x1E = integrity error, likely CRC or IV)")
