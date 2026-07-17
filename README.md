<!-- IMAGE: hero shot — the train with headlights lit, card on the reader.
     ![Evil Avenue P3](images/hero.jpg) -->

# Evil Avenue · Project 3
### An encrypted NFC fare terminal built on a Raspberry Pi and PN532, using MIFARE DESFire EV3 smartcards.

An OMNY-style transit payment system. Tap a card and it authenticates cryptographically, reads the balance stored on the card, charges the fare, and flashes the train's headlights green. Tap a card it doesn't have the key for and it rejects it.

Two modes share one backend. **Terminal** charges $3.00 (the 2026 MTA base fare) and gives one free transfer per fare, matching the real MTA rule. **Reload kiosk** tops up a card, requiring both the Coco Bank card and a PIN.

Built June–July 2026. Everything from the SPI wiring to the DESFire command bytes to the enclosures was built from scratch.

---

## Hardware

| | |
|---|---|
| **Compute** | Raspberry Pi Zero 2 W · Raspberry Pi OS Lite (headless) |
| **NFC** | ElecHouse PN532 V3 over SPI · raw DESFire APDUs via `InDataExchange` |
| **Cards** | 3x MIFARE DESFire EV3 4K, all reprovisioned to owned keys |
| **Lighting** | 2x bi-color LED headlights · GPIO 17/27 push-pull pair |
| **Body** | Diecast MTA subway train on an elevated stand · two 3D-printed enclosures I designed |
| **Software** | Python · Flask · pycryptodomex · systemd |

Three zones on one stand: reader up front where you tap, train elevated in the middle with headlights facing forward, Pi at the rear in its own enclosure.

<!-- IMAGE: the full rig side-on, powered.
     ![Full build](images/full-build.jpg) -->

---

# The build

What follows is what actually went wrong and what fixed it.

## Wiring

**The chip select had to be driven in software.** The Pi's SPI overlay is set to `spi0-0cs`, meaning zero hardware chip-select lines. The PN532's CS pin gets toggled by code through GPIO 8 (physical pin 24) instead. If that wire moves anywhere else, the reader half-works — short exchanges get through, longer ones fail — which looks exactly like a broken module.

**SPI isn't crossed like UART.** MOSI goes to MOSI, MISO goes to MISO. Wiring them crossed the way you would TX/RX gives you a reader that never talks.

**The LED plan was wrong before it was built.** The bulbs were sold as dual-color, and the assumption was 3-leg common-cathode: one leg per color, one shared ground. They turned out to be **2-pin bi-color anti-parallel** — two LED dies wired back to back inside one package, no common pin at all. Color isn't selected by choosing a leg; it's selected by *polarity*.

That means a push-pull pair. GPIO 17 high and GPIO 27 low gives one color. Reverse it for the other. Both low is off. Both headlights sit in parallel across the same two pins in the same orientation, so they always match, with one 220Ω resistor per LED. Current only ever flows through one die at a time.

**220Ω is the right value for a 3.3V GPIO.** Ohm's law puts a red LED at roughly 130Ω for full brightness, but 220Ω keeps current near 6mA per LED — safely under the pin's ~16mA limit, still bright enough to read through the shell, and it works for both colors despite their different forward voltages.

## Soldering

The SPI header on the PN532 was the first thing I ever soldered.

**Nothing is soldered to the Pi.** The GPIO header is the interface — resistors get soldered to the LED legs, a female jumper end gets cut and soldered to the resistor, and the female end plugs onto the pin. The Pi stays modular and replaceable.

The diecast train came with factory lights and audio. They were killed during teardown — the factory joints were poor and didn't survive. That turned out fine: the empty shell became a cleaner canvas for custom headlights than the originals ever were.

One LED joint needed reflowing after mounting. Worth knowing: if only one headlight lights, that's the joint, not the code.

## Cryptography

**Talking to the card at all.** The PN532 will forward raw DESFire APDUs if you wrap them correctly:

```python
pn532.call_function(0x40, params=[0x01, cmd] + data)
```

That's `InDataExchange`. Everything else is built on it. No library does this part.

**ChangeKey and the `0x1E` wall.** ChangeKey replaces a card's factory key with your own. It's the hardest operation in the DESFire spec — it returns a single integrity error, `0x1E`, with no information about what's wrong, and people spend weeks on it.

Two things fixed it:

1. **The CRC.** DESFire's CRC32 is standard CRC32 *without* the final XOR-out. `zlib.crc32` applies that XOR, so it has to be un-applied: `zlib.crc32(data) ^ 0xFFFFFFFF`
2. **The IV.** The key material is encrypted with a **zero IV**, not the running one.

Status went from `0x1E` to `0x00` the moment the CRC was corrected. All three cards were then reprovisioned onto a private AES-128 key. The factory default now returns `0xAE` — authentication error. Only my key works.

**The same lesson solved random UID.** Enabling random UID uses `SetConfiguration` (`0x5C`), authenticated to the card master key, which is still factory DES — so it's a DES-encrypted path, not AES. The first attempt returned `0x1E` again. The fix was the same: **IV = zeros**. That flip is permanent and irreversible, so it was only ever done on a card designated for it.

**Session key derivation** for legacy AES auth (`0xAA`):
```
session_key = RndA[0:4] + RndB[0:4] + RndA[12:16] + RndB[12:16]
```

## The identification rebuild

Most NFC systems identify a card by its UID. The UID is broadcast in the clear before any authentication happens, and it can be cloned trivially. Building fare logic on it means trusting a value anyone can copy.

Once random UID was enabled on one card, this stopped being theoretical — that card presents a different UID every tap, so it never matched itself between taps. Free transfers stopped working for it, because the transfer logic was keyed on UID.

The easy fix was to call it a demo card and move on. The correct fix was to stop trusting the UID entirely.

Now every card carries a card ID in a protected data file inside its application, readable only after authentication. The terminal identifies by that. The random-UID card works completely — including transfers — because the UID was never what mattered.

<!-- IMAGE: watch_uid output — random card cycling UIDs, static card fixed.
     ![Random UID](images/random-uid.jpg) -->

## Reliability

**Charging on a failed read.** The worst bug in the project. A debit would commit on the card, the response frame would get garbled on the way back, and the code would report "try again" — so you'd tap again and get charged twice. The money left the card; the confirmation just didn't arrive.

The fix is idempotency: after an unclear debit response, read the actual balance and see what really happened before deciding what to tell the user.

**"Unknown" on a card that was clearly ours.** Card identification worked by trying to select each application. When a read glitched, both selects failed, and the code concluded "unrecognized card" — when the truth was "couldn't read it." Those are different answers and they need different messages. Now a clean-but-unmatched read reports unknown; a glitched read reports retry.

**Security made it slow.** Adding the auth gate and the internal ID read roughly doubled the card operations per tap. In practice you hover a card over a reader for maybe half a second — the operations no longer fit in that window, so you'd get an approval followed by errors as you pulled away and the later reads failed.

Fixed by streamlining rather than adding retries: one auth (needed anyway), one balance read, trust the debit response unless it looks wrong. Then detection and cooldown were separated — a fast 0.2s poll keeps it sensitive, and a 2.0s window after each tap silently discards reads so one hover produces exactly one action.

## Troubleshooting

**Patch scripts fail silently.** Most of the code was edited by Python scripts doing find-and-replace on anchor strings. When an anchor doesn't match exactly, the replacement just doesn't happen — no error. One of these left a variable used but never defined, and the reader stopped responding entirely.

Lesson: grep to verify a patch landed before relaunching.

**Grep for the feature, not your variable name.** Time was lost "fixing" a red-on-zero-balance display that already worked, because the check searched for a variable name from a patch that never ran. The live code used a different name. The feature was there the whole time.

**A missing file looks exactly like a hardware bug.** After rewiring, one command read cards and another didn't — a textbook symptom of a marginal connection. The wiring was fine. The second script had been lost in a rebuild months earlier and didn't exist.

**Only one process can own the reader.** A background service holding the SPI bus makes every standalone script fail in confusing ways. Stop the service first.

**Flask caches templates.** With `debug=False`, editing the HTML does nothing until the service restarts. A browser refresh isn't enough.

**Silence can be the correct output.** The UID watcher only prints when the UID *changes*. Tapping the static card looked like the script had frozen. It hadn't — a card that never changes its UID has nothing new to print. The silence was the proof.

## Commands and Linux

**Bracketed paste corrupts here-docs over SSH.** Pasting large code blocks would inject `[200~` markers into files and break them. `printf '\e[?2004l'` at the start of each session disables it.

**mDNS is unreliable.** `ssh cocozero@rfid-pi.local` works until it doesn't, and the IP changes on reboot. `hostname -I` on the Pi gets the current one.

**It runs as a service now.** `omny.service` starts on boot with `Restart=on-failure`, replacing the original service that conflicted over the SPI pins. Plug the train in and the terminal comes up on its own — reader online, headlights self-testing, web UI live.

**No WiFi, no problem.** If no known network is available, the Pi brings up its own hotspot. The whole system works in a room with no infrastructure. The failover is one-way by design: once it commits to standalone it stays there until rebooted, so a stray network can't pull it off mid-demo.

**Backups have to be verified.** The SD card is imaged with `dd` piped through `gzip`, then checked with `gzip -t` — which decompresses the entire archive to confirm it's readable. An untested backup is a guess.

## 3D printing and layout

Two enclosures, both designed and printed on my own machine: one for the Pi, one for the reader.

The original plan was a Gridfinity-based exoskeleton — a modular baseplate holding the Pi, the train, and the reader, with external clip-in wire channels and exposed wiring as a deliberate aesthetic. It was fully designed on paper.

It got cut. A phone stand held everything at the right height, and the enclosure work that actually mattered was the two housings. Dropping it was the right call — it was a real design project that didn't make the machine work any better.

---

## Security model

This is EMV-*style* — it borrows the architecture of card payment systems, implemented on DESFire:

- Card-present cryptographic authentication (AES-128 mutual auth)
- Stored value on the card, not in a database
- Atomic debit with commit
- Cardholder verification (card + PIN)
- Server-side transaction limits
- Tamper-evident audit trail (HMAC-SHA256, each entry chained to the previous signature — editing any past entry breaks the chain and the verifier reports the exact line)

It is **not** real EMV. There's no issuer PKI, no application cryptograms, and no certification. "EMV-inspired" is the accurate label.

Known limits, stated plainly: the card master key is still the factory default — deliberately, since that's what allows the random UID configuration. Fare operations run in plain communication mode rather than encrypted. The HMAC signing key lives in source. Transaction state is in memory and resets on restart. There's no transaction counter; rollback protection relies on the fact that writing to a card requires the key.

---

## Demo

Tap a card — the fare comes off and the headlights go green. Tap again within the window — free transfer, balance unchanged. Reload it with the bank card and PIN. Tap a foreign card — rejected at the auth gate. Tap the random-UID card — different UID every time, rides fine. Edit the transaction log — the verifier catches the exact line.

The reload cap is $60 in the UI. Sending a raw request that skips the browser entirely still gets rejected, because the cap is enforced server-side.

---

*Evil Avenue · New York City*
**[evilavenue.com](https://evilavenue.com)**

<!-- Additional images worth adding:
     - the two 3D-printed enclosures
     - close-up of the PN532 wiring
     - reload kiosk UI with the two factor indicators
     - the tamper check catching a modified log -->
