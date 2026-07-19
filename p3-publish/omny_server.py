import time
import json
import queue
import threading
import board
import busio
from digitalio import DigitalInOut
from adafruit_pn532.spi import PN532_SPI
from flask import Flask, Response, render_template, request, jsonify
import txlog
import led_controller

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = DigitalInOut(board.D8)
pn532 = PN532_SPI(spi, cs, debug=False)
ic, ver, rev, support = pn532.firmware_version
print(f"[omny] PN532 firmware {ver}.{rev} online")
pn532.SAM_configuration()

COCO_APP = [0xC0, 0xC0, 0xC0]
FARE_APP = [0x01, 0x00, 0x00]
FILE_ID = 0x01
FARE = 300
TRANSFER_WINDOW = 120
PIN = "REPLACE_ME"
MAX_RELOAD = 6000   # $60.00 server-side cap

# --- Owned-key authentication (Evil Avenue key) ---
import os as _os
from Cryptodome.Cipher import AES as _AES
EVIL_KEY = bytes.fromhex("REPLACE_WITH_YOUR_OWN_AES128_KEY")
ID_FILE = 0x02

def read_card_id():
    """Read the stable internal card ID (after auth). Returns int or None."""
    try:
        offset = list((0).to_bytes(3, "little"))
        length = list((4).to_bytes(3, "little"))
        status, out = cmd(0xBD, bytes([ID_FILE] + offset + length))
        if status == 0x00 and len(out) >= 4:
            return int.from_bytes(out[:4], "little")
    except ShortResponse:
        pass
    return None

def _rotl(b): return b[1:]+b[:1]
def _rotr(b): return b[-1:]+b[:-1]

def aes_auth_evil(key_no=0):
    """AES-authenticate to the currently-selected app with the Evil Avenue key.
       Returns True if the card proves it knows our key."""
    try:
        status, enc = cmd(0xAA, bytes([key_no]))
        if status != 0xAF or len(enc) < 16:
            return False
        enc = enc[:16]
        rndB = _AES.new(EVIL_KEY, _AES.MODE_CBC, iv=bytes(16)).decrypt(enc)
        rndA = _os.urandom(16)
        token = _AES.new(EVIL_KEY, _AES.MODE_CBC, iv=enc).encrypt(rndA + _rotl(rndB))
        status, enc2 = cmd(0xAF, token)
        if status != 0x00 or len(enc2) < 16:
            return False
        rndA_card = _rotr(_AES.new(EVIL_KEY, _AES.MODE_CBC, iv=token[-16:]).decrypt(enc2[:16]))
        return rndA_card == rndA
    except ShortResponse:
        return False

class ShortResponse(Exception):
    pass

def cmd(c, data=b""):
    payload = [0x01, c] + list(data)
    resp = pn532.call_function(0x40, params=payload, response_length=64)
    if resp is None or len(resp) < 2:
        raise ShortResponse("short")
    return resp[1], bytes(resp[2:])

def le_bytes(n, size):
    return list(n.to_bytes(size, "little", signed=True))

def select(app):
    try:
        status, _ = cmd(0x5A, bytes(app))
        return status == 0x00
    except ShortResponse:
        return False

def get_balance():
    status, out = cmd(0x6C, bytes([FILE_ID]))
    if status != 0x00 or len(out) < 4:
        return None
    return int.from_bytes(out[:4], "little", signed=True)

def get_balance_safe():
    for _ in range(2):
        try:
            b = get_balance()
            if b is not None:
                return b
        except ShortResponse:
            pass
        time.sleep(0.02)
    return None

def debit(amount):
    status, _ = cmd(0xDC, bytes([FILE_ID] + le_bytes(amount, 4)))
    if status != 0x00:
        return False
    status, _ = cmd(0xC7)
    return status == 0x00

def credit(amount):
    status, _ = cmd(0x0C, bytes([FILE_ID] + le_bytes(amount, 4)))
    if status != 0x00:
        return False
    status, _ = cmd(0xC7)
    return status == 0x00

def select_raw(app):
    try:
        status, _ = cmd(0x5A, bytes(app))
        return (status == 0x00, True)
    except ShortResponse:
        return (False, False)

def identify():
    for _ in range(2):
        coco_ok, coco_clean = select_raw(COCO_APP)
        if coco_ok:
            return "coco"
        fare_ok, fare_clean = select_raw(FARE_APP)
        if fare_ok:
            return "fare"
        if coco_clean and fare_clean:
            return "unknown"
        time.sleep(0.02)
    return "retry"

transfer_state = {}

def process_ride(uid_hex):
    # Lean tap: identify -> auth (needed anyway) -> read ID -> balance -> debit
    kind = identify()
    if kind == "retry":
        return event("retry", uid_hex)
    if kind == "coco":
        return event("wrongcard", uid_hex, message="Use a Fare Card to Ride")
    if kind == "unknown":
        return event("unknown", uid_hex)
    # single auth (this is our access-control gate AND enables the ID read)
    if not aes_auth_evil():
        return event("unknown", uid_hex, message="Card Not Authorized")
    card_id = read_card_id()
    if card_id is None:
        return event("retry", uid_hex)
    ident = f"card{card_id}"
    bal_before = get_balance()  # single read; we're authenticated & fresh
    if bal_before is None:
        return event("retry", uid_hex)
    st = transfer_state.get(ident, {"last_paid": 0, "transfer_used": True})
    now = time.time()
    within_window = (now - st["last_paid"]) <= TRANSFER_WINDOW
    if within_window and not st["transfer_used"]:
        st["transfer_used"] = True
        transfer_state[ident] = st
        return event("transfer", uid_hex, balance=bal_before, message="Transfer - Free")
    if bal_before < FARE:
        return event("insufficient", uid_hex, balance=bal_before)
    # debit; trust the success response, only verify if it looked bad
    try:
        ok = debit(FARE)
    except ShortResponse:
        ok = False
    if ok:
        transfer_state[ident] = {"last_paid": now, "transfer_used": False}
        return event("approved", uid_hex, balance=bal_before - FARE, message="Go")
    # debit response unclear - verify actual balance once
    bal_after = get_balance_safe()
    if bal_after is not None and bal_after == bal_before - FARE:
        transfer_state[ident] = {"last_paid": now, "transfer_used": False}
        return event("approved", uid_hex, balance=bal_after, message="Go")
    return event("retry", uid_hex)


def event(kind, uid_hex, balance=None, message=None):
    labels = {
        "approved":("APPROVED","Go"), "transfer":("TRANSFER","Transfer - Free"),
        "insufficient":("DENIED","Insufficient Balance"), "retry":("TRY AGAIN","Please Try Again"),
        "unknown":("UNKNOWN","Card Not Recognized"), "wrongcard":("WRONG CARD","Use a Fare Card to Ride"),
        "reloaded":("RELOADED","Balance Added"), "cardok":("CARD OK","Enter PIN to Authorize"),
        "balance":("BALANCE","Card Balance"),
    }
    status, default_msg = labels[kind]
    ev = {"result":kind,"status":status,"message":message or default_msg,
          "uid":uid_hex,"time":time.strftime("%H:%M:%S")}
    if balance is not None:
        ev["balance"]=balance; ev["balance_str"]=f"${balance/100:.2f}"
    return ev

subscribers = []
subscribers_lock = threading.Lock()

def broadcast(ev):
    data = json.dumps(ev)
    try:
        led_controller.signal(ev.get("result"))
    except Exception:
        pass
    with subscribers_lock:
        for q in subscribers:
            q.put(data)

mode = {"current":"terminal"}
# two-factor reload state: card_verified AND pin_verified both needed
reload_flow = {"amount":0, "card_verified":False, "pin_verified":False, "check":False}

def is_authorized():
    return reload_flow["card_verified"] and reload_flow["pin_verified"]

def scan_loop():
    last_action=0
    COOLDOWN=2.0   # after a tap, silently ignore reads for this long (your manual cooldown)
    while True:
        # fast, sensitive detection - short timeout keeps it responsive
        uid = pn532.read_passive_target(timeout=0.2)
        if uid is None:
            continue
        now=time.time()
        # COOLDOWN GATE: we detected a card, but if we just acted, discard it silently
        if (now - last_action) < COOLDOWN:
            continue
        uid_hex=bytes(uid).hex()
        last_action=now
        try:
            if mode["current"]=="terminal":
                ev=process_ride(uid_hex); broadcast(ev)
                bal_c = ev.get("balance", -1)
                amt_c = FARE if ev["result"]=="approved" else 0
                txlog.log_tx(uid_hex, ev["result"], amt_c, bal_c, ev["status"])
                print(f"[omny] RIDE {uid_hex} -> {ev['status']} {ev.get('balance_str','')}")
            else:
                handle_reload_tap(uid_hex)
        except ShortResponse:
            broadcast(event("retry",uid_hex))
        except Exception as e:
            print(f"[omny] scan error: {e}")

def handle_reload_tap(uid_hex):
    kind = identify()
    if reload_flow["check"]:
        if kind=="fare":
            b=get_balance_safe()
            if b is None: broadcast(event("retry",uid_hex))
            else: broadcast(event("balance",uid_hex,balance=b,message="Card Balance"))
        elif kind=="coco":
            broadcast(event("balance",uid_hex,message="Coco Bank (no balance)"))
        else:
            broadcast(event("unknown",uid_hex))
        return
    # if fully authorized (card + pin), a fare card tap loads it
    if is_authorized():
        if kind=="fare":
            select(FARE_APP)
            if not aes_auth_evil():
                print(f"[omny] AUTH GATE FAILED (reload load) {uid_hex}")
                broadcast(event("unknown",uid_hex,message="Card Not Authorized"))
                return
            amt=reload_flow["amount"]
            if amt <= 0 or amt > MAX_RELOAD:
                print(f"[omny] CREDIT BLOCKED - amount {amt} outside 1..{MAX_RELOAD}")
                broadcast(event("retry",uid_hex,message="Invalid Amount")); return
            bal=get_balance_safe()
            if bal is None: broadcast(event("retry",uid_hex)); return
            if credit(amt):
                nb=get_balance_safe() or (bal+amt)
                reload_flow.update({"amount":0,"card_verified":False,"pin_verified":False})
                broadcast(event("reloaded",uid_hex,balance=nb,message=f"Added ${amt/100:.2f}"))
                txlog.log_tx(uid_hex, "reload", amt, nb, "RELOADED")
                print(f"[omny] RELOAD {uid_hex} +${amt/100:.2f} -> ${nb/100:.2f}")
            else:
                broadcast(event("retry",uid_hex))
        else:
            broadcast(event("wrongcard",uid_hex,message="Tap a Fare Card to Load"))
        return
    # not yet authorized: this tap is the Coco Bank card presenting factor 1
    if kind=="coco":
        # AUTH GATE: Coco Bank card must prove it knows the Evil Avenue key
        select(COCO_APP)
        if not aes_auth_evil():
            print(f"[omny] AUTH GATE FAILED (coco) {uid_hex}")
            broadcast(event("wrongcard",uid_hex,message="Card Not Authorized"))
            return
        reload_flow["card_verified"]=True
        if reload_flow["pin_verified"]:
            broadcast(event("cardok",uid_hex,message="Authorized - Tap Fare Card"))
        else:
            broadcast(event("cardok",uid_hex,message="Card OK - Enter PIN to Authorize"))
        print(f"[omny] RELOAD Coco Bank verified {uid_hex} (pin={'ok' if reload_flow['pin_verified'] else 'pending'})")
    else:
        broadcast(event("wrongcard",uid_hex,message="Tap Coco Bank Card to Pay"))

app = Flask(__name__)

@app.route("/")
def index(): return render_template("omny.html")

@app.route("/mode", methods=["POST"])
def set_mode():
    m=request.json.get("mode")
    if m in ("terminal","reload"):
        mode["current"]=m
        reload_flow.update({"amount":0,"card_verified":False,"pin_verified":False,"check":False})
    return jsonify({"mode":mode["current"]})

@app.route("/reload/amount", methods=["POST"])
def reload_amount():
    amount=int(request.json.get("amount",0))
    if amount <= 0 or amount > MAX_RELOAD:
        print(f"[omny] RELOAD REJECTED - amount {amount} outside 1..{MAX_RELOAD}")
        return jsonify({"error":"over_limit","max":MAX_RELOAD})
    if amount <= 0:
        return jsonify({"error":"invalid","amount":0})
    reload_flow.update({"amount":amount,"check":False})
    return jsonify({"amount":amount,"authorized":is_authorized()})

@app.route("/reload/pin", methods=["POST"])
def reload_pin():
    pin=request.json.get("pin","")
    if pin==PIN:
        reload_flow["pin_verified"]=True
        return jsonify({"pin_ok":True,"authorized":is_authorized(),"card_verified":reload_flow["card_verified"]})
    reload_flow["pin_verified"]=False
    return jsonify({"pin_ok":False})

@app.route("/reload/check", methods=["POST"])
def reload_check():
    reload_flow.update({"check":True,"card_verified":False,"pin_verified":False,"amount":0})
    return jsonify({"check":True})

@app.route("/stream")
def stream():
    def gen():
        q=queue.Queue()
        with subscribers_lock: subscribers.append(q)
        try:
            yield f"data: {json.dumps({'result':'ready','mode':mode['current']})}\n\n"
            while True: yield f"data: {q.get()}\n\n"
        finally:
            with subscribers_lock: subscribers.remove(q)
    return Response(gen(), mimetype="text/event-stream")

if __name__ == "__main__":
    threading.Thread(target=scan_loop, daemon=True).start()
    led_controller.start()
    app.run(host="0.0.0.0", port=5000, threaded=True)
