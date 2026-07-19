"""
Evil Avenue - Signed Transaction Log
Each entry is HMAC-signed and chained to the previous entry's signature.
Altering or deleting any past entry breaks the chain - tamper evident.
"""
import json
import hmac
import hashlib
import os
import time

LOG_PATH = "/home/cocozero/rfid/transactions.log"
# In production this key would live in secure storage / env, not source.
SIGNING_KEY = b"REPLACE_WITH_YOUR_OWN_HMAC_KEY"
GENESIS = "0" * 64  # first entry chains from this

def _sign(prev_sig, entry_bytes):
    mac = hmac.new(SIGNING_KEY, prev_sig.encode() + entry_bytes, hashlib.sha256)
    return mac.hexdigest()

def _last_sig():
    if not os.path.exists(LOG_PATH):
        return GENESIS
    last = None
    with open(LOG_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                last = line
    if not last:
        return GENESIS
    try:
        return json.loads(last)["sig"]
    except Exception:
        return GENESIS

def log_tx(uid, action, amount_cents, balance_cents, result):
    """Append a signed transaction entry."""
    prev_sig = _last_sig()
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "uid": uid,
        "action": action,          # ride / transfer / reload / denied / etc.
        "amount": amount_cents,
        "balance": balance_cents,
        "result": result,
        "prev": prev_sig,
    }
    entry_bytes = json.dumps(entry, sort_keys=True).encode()
    sig = _sign(prev_sig, entry_bytes)
    entry["sig"] = sig
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return sig

def verify_log():
    """Walk the chain, confirm every signature and link. Returns (ok, count, err)."""
    if not os.path.exists(LOG_PATH):
        return True, 0, None
    prev_sig = GENESIS
    count = 0
    with open(LOG_PATH, "r") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                return False, count, f"line {i}: not valid JSON"
            stored_sig = entry.pop("sig", None)
            if entry.get("prev") != prev_sig:
                return False, count, f"line {i}: broken chain (prev mismatch)"
            entry_bytes = json.dumps(entry, sort_keys=True).encode()
            expected = _sign(prev_sig, entry_bytes)
            if not hmac.compare_digest(expected, stored_sig or ""):
                return False, count, f"line {i}: bad signature (tampered)"
            prev_sig = stored_sig
            count += 1
    return True, count, None

if __name__ == "__main__":
    # running directly = verify the log
    ok, count, err = verify_log()
    if ok:
        print(f"Log OK - {count} entries, chain intact")
    else:
        print(f"Log INVALID after {count} entries: {err}")
