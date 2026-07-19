import board, busio, os
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI
from Cryptodome.Cipher import AES

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
pn532.SAM_configuration()

FARE_APP = [0x01, 0x00, 0x00]
DEFAULT_AES_KEY = bytes(16)

def cmd(c, data=b""):
    payload = [0x01, c] + list(data)
    resp = pn532.call_function(0x40, params=payload, response_length=64)
    if resp is None or len(resp) < 2:
        return None, b""
    return resp[1], bytes(resp[2:])

def rotl(b): return b[1:] + b[:1]
def rotr(b): return b[-1:] + b[:-1]

def aes_authenticate(key, key_no=0):
    # Step 1: start AES auth (0xAA)
    status, enc_rndB = cmd(0xAA, bytes([key_no]))
    if status != 0xAF or len(enc_rndB) < 16:
        print(f"  auth start failed: status 0x{status:02x}, len {len(enc_rndB)}")
        return None
    enc_rndB = enc_rndB[:16]
    print(f"  RndB_enc: {enc_rndB.hex()}")

    # Step 2: decrypt RndB (AES-CBC, zero IV)
    rndB = AES.new(key, AES.MODE_CBC, iv=bytes(16)).decrypt(enc_rndB)
    print(f"  RndB:     {rndB.hex()}")

    rndB_rot = rotl(rndB)
    rndA = os.urandom(16)
    print(f"  RndA:     {rndA.hex()}")

    # Step 3: encrypt (RndA + RndB_rot), IV = enc_rndB
    token = AES.new(key, AES.MODE_CBC, iv=enc_rndB).encrypt(rndA + rndB_rot)

    # Step 4: send 0xAF + token
    status, enc_rndA_rot = cmd(0xAF, token)
    if status != 0x00 or len(enc_rndA_rot) < 16:
        print(f"  auth part2 failed: status 0x{status:02x}")
        return None
    enc_rndA_rot = enc_rndA_rot[:16]

    # Step 5: decrypt card's response (IV = last 16 of token), verify RndA
    rndA_rot_card = AES.new(key, AES.MODE_CBC, iv=token[-16:]).decrypt(enc_rndA_rot)
    rndA_card = rotr(rndA_rot_card)
    if rndA_card != rndA:
        print(f"  RndA mismatch! got {rndA_card.hex()}")
        return None

    # Step 6: derive session key (legacy AES): RndA[0:4]+RndB[0:4]+RndA[12:16]+RndB[12:16]
    session_key = rndA[0:4] + rndB[0:4] + rndA[12:16] + rndB[12:16]
    print(f"  [+] AES AUTH OK")
    print(f"  Session key: {session_key.hex()}")
    return session_key

print("Tap a fare card...")
uid = pn532.read_passive_target(timeout=10)
if uid is None:
    print("No card.")
else:
    print(f"Card UID: {bytes(uid).hex()}")
    cmd(0x5A, bytes(FARE_APP))   # select fare app
    print("AES authenticating to fare app (default key)...")
    aes_authenticate(DEFAULT_AES_KEY)
