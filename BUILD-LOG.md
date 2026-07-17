# Project 3 — Build Log

A retrospective on building an encrypted NFC fare terminal from scratch. June 15 – July 16, 2026.

This is the long version: what I set out to make, what went wrong, what I learned, and what I chose not to do.

---

## The idea

I wanted to build a payment terminal.

Not a card reader that blinks when it sees a card — a working stored-value payment system with real cryptography, housed in something worth looking at. The specific picture I had before I knew how to build any of it:

A flat terminal with an NFC logo. Three cards standing in for real OMNY cards: one with $15 that taps and goes, one with $1 that gets denied, one with $3 that rides once and then gets a free transfer. A diecast MTA subway train with an indicator light that reacts to every tap. A screen that sits white by default and blinks green or red with a message. On launch, a POV animation like standing behind a subway door as it opens.

Underneath: AES mutual authentication, randomized UIDs for privacy, EMV-style fare logic, signed transaction logging. Built from the chip up.

That picture existed on day one. Almost all of it got built. The parts that didn't were cut on purpose, and that's covered further down.

The reason it's a fare terminal and not something else: I'm aiming at bank and fintech cybersecurity. Payment security is that industry's whole world. Most student projects are adjacent to what a bank cares about. I wanted one that landed in the middle of it.

---

## Planning

Two decisions early on shaped everything.

**Don't rebuild what exists.** The temptation with a crypto project is to implement AES yourself. That's a trap — you'd spend the project on a solved problem and end up with something worse than a library. The actual gap was that no library speaks DESFire over a PN532 in SPI mode. So the plan was: use a real crypto library for the primitives, and build the thin transport bridge that didn't exist. Understand the crypto well enough to explain every byte, don't reimplement it.

**Product first, hardening second.** Security features are invisible in a demo and break things easily when added early. Get the machine working end to end, then layer security onto a foundation that already runs. This turned out to be right — every hardening step later went onto something I could immediately test.

The framing was also settled early: this is **EMV-style**, not EMV. Real EMV is a licensed, certified standard with issuer PKI and application cryptograms. I'm not doing that and neither is any personal project. Calling it EMV-inspired is accurate and it's the one label that protects the credibility of everything else.

---

## Sourcing

- **Raspberry Pi Zero 2 W** — small enough to disappear into the build, capable enough to run the whole stack.
- **ElecHouse PN532 V3** — the red board. Supports SPI, I2C, and UART via DIP switches. SPI for speed and reliability.
- **MIFARE DESFire EV3 4K** ×3 — the real thing. Same family transit systems actually use. Not NTAG, not Classic.
- **Diecast MTA subway train** — the body.
- **LEDs** — bought as "5mm dual-color red & yellow/green" from Microcenter, ~$7. What they actually were became a whole problem of its own; see Wiring.
- **Resistors** — started with what was on hand. 1kΩ was too dim, 10kΩ wouldn't light at all. 220Ω was the right value and had to be ordered online (EDGELEC, 1/2W). This blocked the LED work for about a week, which turned out fine — there was plenty of software left.
- **Adafruit Premium Female/Female jumpers** — worth the money. Cheap jumpers with loose crimps cause intermittent faults that look like software bugs.
- **Bambu Lab P1S** — already owned, used for both enclosures.

---

## Wiring

**Chip select is driven in software.** The Pi's SPI overlay is set to `spi0-0cs` in `/boot/firmware/config.txt`, which means zero hardware chip-select lines. The PN532's CS pin gets toggled by code through GPIO 8 — physical pin 24. If that wire is anywhere else, the reader half-works: short exchanges get through, longer ones fail. That looks exactly like a broken module.

```
SCK   → pin 23  (GPIO 11)
MOSI  → pin 19  (GPIO 10)
MISO  → pin 21  (GPIO 9)
CS/SS → pin 24  (GPIO 8)
VCC   → pin 2 or 4  (same 5V rail)
GND   → any ground
```

**SPI is not crossed like UART.** MOSI goes to MOSI, MISO goes to MISO. Wiring them crossed the way you would TX/RX gives a reader that never talks.

**The LED plan was wrong before it was built.** I'd planned for 3-leg common-cathode bulbs: one leg per color, one shared ground, four resistors, a shared GPIO. Every note I'd written assumed that.

They were **2-pin bi-color anti-parallel** — two LED dies wired back to back inside one package, no common pin. Color isn't chosen by picking a leg. It's chosen by polarity.

So it's a push-pull pair:

```
GPIO 17 HIGH, GPIO 27 LOW   → one color
GPIO 17 LOW,  GPIO 27 HIGH  → the other
both LOW                     → off
```

Both headlights sit in parallel across the same two pins in the same orientation, so they always match. One 220Ω resistor per LED. Current only flows through one die at a time.

**Why 220Ω.** Ohm's law on a 3.3V GPIO with a red LED (~2.0V forward) at 10–15mA gives 87–130Ω for full brightness. 220Ω runs it near 6mA — comfortably under the pin's ~16mA limit, still bright enough to read through the shell, and it works for both colors despite their different forward voltages.

**Nothing gets soldered to the Pi.** The GPIO header is the interface. Resistors solder to the LED legs, a female jumper end gets cut and soldered to the resistor, and the female end plugs onto the pin. The Pi stays modular.

---

## Soldering

The SPI header on the PN532 was the first thing I ever soldered.

The diecast train came with factory lights and audio. They died during teardown — the factory joints were poor and didn't survive being opened. That ended up being fine. An empty shell was a better canvas for custom headlights than the originals ever were, and it forced the LED work to be mine rather than a reuse of someone else's wiring.

One LED joint needed reflowing after mounting. Worth writing down: **if only one headlight lights, that's the joint, not the code.** The software has no way to light them separately — they're on the same two pins.

---

## Backend

**Talking to the card at all.** This was the first real unknown — could I reach the DESFire crypto layer through the PN532 over SPI? The answer is `InDataExchange`:

```python
pn532.call_function(0x40, params=[0x01, cmd] + data)
```

That wraps a raw DESFire APDU and forwards it to the card. Everything in the project is built on that one line. No library does this part.

**Identifying the card.** `GetVersion` (0x60) came back NXP DESFire EV3, 4K. The master key type mattered: `0xAA` (AES auth) returned `0xAE`, but `0x1A` (DES auth) returned an 8-byte challenge. So the factory master key is DES, not AES. That detail turned out to matter much later, for random UID.

**DES mutual authentication** worked on the first attempt. Three-pass challenge-response: the card sends an encrypted random, you decrypt it, rotate it, send it back along with your own random, and the card returns yours rotated. Both sides prove they know the key without either transmitting it. The RndA I generated came back matching exactly.

**AES authentication and session keys.** Same shape, 16-byte blocks. The session key derivation for legacy AES auth (`0xAA`):

```
session_key = RndA[0:4] + RndB[0:4] + RndA[12:16] + RndB[12:16]
```

This piece got deferred for weeks because nothing needed it yet. ChangeKey is what forced it — you can't encrypt new key material without a session key.

**Stored value.** DESFire has native value files with credit/debit/commit built in — designed exactly for transit. That's the correct choice over rolling your own with a data file:

```
CreateValueFile  0xCC   (with lower/upper limits and initial value)
GetValue         0x6C
Debit            0xDC
Credit           0x0C
CommitTransaction 0xC7
```

The Commit is what makes it safe. Credit and Debit stage a change; nothing is real until Commit. Pull the card mid-transaction and the pending change is discarded — no corrupted balance, no money created or destroyed. That's the atomicity that makes stored-value cards trustworthy.

**A useful discovery:** the cards allowed creating applications and files in plain communication mode, without authentication. That meant provisioning worked without building the full encrypted-session CMAC layer first. It also meant that later, after changing the keys, cards still rode normally — because the fare operations don't require auth with these file settings. That's a real limitation, and it's stated in the security section.

**Signed logging.** Every transaction writes a JSON line with an HMAC-SHA256 signature computed over the entry *and the previous entry's signature*. Editing any past entry breaks the chain from that point on. The verifier walks it and reports the exact line where it fails.

**The auth gate.** After the cards were reprovisioned onto my own key, the terminal was updated to authenticate before any operation. A card that can't prove it knows the key gets rejected before anything else happens. That's cryptographic access control — a cloned UID doesn't get you in.

---

## Frontend

**The constraint that decided the architecture:** Raspberry Pi OS Lite has no desktop. A GUI window has nowhere to render.

So the UI is a Flask web server on the Pi, serving HTML that any device on the network can open. Tap results push to the browser in real time over Server-Sent Events. That turned out better than a local GUI would have been — it works on a laptop, a phone, or a touchscreen, and it's the same stack as evilavenue.com.

What got built:

- The subway door animation on load — two doors that slide apart, from the original idea sketch.
- A four-state color system: green for approved and transfer, yellow for retry, white for unrecognized, red for insufficient balance. Each state changes the background gradient, the NFC ring, and the status text together.
- Two modes as tabs sharing one backend: **Terminal** and **Reload**.
- The reload kiosk: amount buttons in $3 multiples up to $24, plus a custom option. Two factor indicator dots that light independently — one for the card, one for the PIN.
- A balance check that reads without charging, and turns red on $0.
- Space Mono throughout, Evil Avenue red as the accent.

---

## Burnout

Somewhere in the middle of this, I hit a wall. The note I wrote at the time was just: "touching the keyboard.."

What I did about it: took weekends as weekends. Went to a concert. Went to a birthday bowling night I almost skipped because leaving the comfort zone felt like the wrong use of the time — went anyway, made new friends. The project sat still for those days.

The distinction that mattered was between **resting** and **drifting**. Resting is deliberate and you come back with energy. Drifting is weeks disappearing without noticing. This was resting. The train was built, the site was live, the domain was bought, and the terminal launched — the momentum was real, it just needed a pause.

The other thing I got clearer on: software is a harder fight for me than hardware. I've been building hardware since I was 13. The crypto layer was genuinely new ground, and that's where the burnout came from. Worth knowing about yourself.

---

## Doing it right

There's one decision in this project that I'd point to over any other, and it wasn't a technical breakthrough.

After random UID went live on one card, that card got flaky. Sometimes it rode, sometimes it threw errors. The transfer feature stopped working for it entirely, because transfers were keyed on UID and the UID changed every tap. The card could never match itself.

The easy fix was right there: call it a demo card. Its job is to *show* random UID, not to be a working fare card. Keep the static card for the real demo. Ship it.

I said: it's not about simple, it's about doing it right.

The correct fix was to admit the UID was never trustworthy in the first place. It's broadcast in the clear, before any authentication, and it can be cloned in seconds. Building fare logic on it means trusting a value anyone can copy. Real card-present systems don't do that — they authenticate first, then read identity from protected data inside the card.

So the identification layer got torn out and rebuilt. Every card now carries an ID in a protected file (`0x02`), readable only after authentication. The terminal identifies by that. Random UID stopped being a special case, because the UID was never what mattered.

That rebuild is the thing I'd lead with in an interview. Not because it was the hardest — ChangeKey was harder — but because the easy path was available and obviously good enough, and taking it would have left a worse system that still demoed fine.

---

## Troubleshooting

Everything that broke, and what it actually was.

### Environment

**Bracketed paste corrupted every large paste over SSH.** Pasting a here-doc would inject `[200~` and `~` markers into the file and break it. Fix: `printf '\e[?2004l'` at the start of each session.

**mDNS is unreliable.** `ssh cocozero@rfid-pi.local` works until it doesn't. The IP changes on reboot. `hostname -I` on the Pi gets the current one.

**Only one process can own the PN532.** The original `rfid.service` auto-started on boot and held the SPI bus. Any standalone script then failed in confusing ways. Same problem with running the Pi's own display and an SSH session at once — they fight over the reader. Stop the service first.

**`Cryptodome`, not `Crypto`.** pycryptodomex uses the capital-C namespace.

### Card communication

**Approve/deny at roughly 50/50.** Early on, the same card would authenticate, then fail, then authenticate. It looked random. It was RF read glitches — the card moving slightly, a dropped frame mid-handshake.

**`bytearray index out of range`.** The PN532 sometimes returns a short or empty response when the field drops mid-exchange. The code indexed into it blindly and crashed. Fix: a `ShortResponse` exception and length checks on every response, so a dropped read becomes a clean failure instead of a stack trace.

**Retries fixed the glitches and created a new problem** — see Reliability below.

### Cryptography

**ChangeKey returned `0x1E` and told me nothing.** ChangeKey replaces a card's factory key with your own. It's the hardest operation in the DESFire spec, it returns a single integrity error with no diagnostic information, and people spend weeks on it. Forums are full of them.

Two things were wrong:

1. **The CRC.** DESFire's CRC32 is standard CRC32 **without** the final XOR-out. `zlib.crc32` applies that XOR, so it has to be un-applied: `zlib.crc32(data) ^ 0xFFFFFFFF`
2. **The IV.** Key material is encrypted with a **zero IV**, not the running one.

The status went `0x1E` → `0x00` the moment the CRC was corrected. The full APDU:

```
0xC4 + keyNo + AES-CBC(session_key, IV=zeros, newKey + version + CRC + zero-pad-to-16)
CRC computed over: [0xC4, keyNo] + newKey + [version]
```

**Random UID returned `0x1E` too.** `SetConfiguration` (0x5C) enables it, authenticated to the card master key — which is still factory DES, so this is a DES-encrypted path, not AES. Different code, same wall.

The fix was the same lesson: **IV = zeros.** I wrote a script that tried four combinations of CRC scope and IV, and variant 2 — CRC over `[0x5C, option, config_byte]`, IV zeros — returned `0x00` on the first run.

That flip is permanent. It was only ever done on one card, chosen in advance, with backups taken first.

### Logic bugs

**The terminal charged you and told you to try again.** The worst bug in the project. A debit would commit on the card, the response frame would get garbled on the way back, and the code reported "try again." You'd tap again and get charged twice. The money left the card; the confirmation just didn't arrive.

Fix: idempotency. After an unclear debit response, read the actual balance and compare it to what it was before deciding what to tell the user. If the money already moved, report approved. This is the same problem real payment systems solve, and hitting it honestly was one of the more instructive moments of the build.

**"Unknown" on cards that were obviously mine.** Card type was identified by trying to select each application. When a read glitched, both selects failed, and the code concluded "unrecognized card." But "I cleanly determined this isn't ours" and "I couldn't read it" are different answers that deserve different messages. Fix: `identify()` now distinguishes a clean not-found from a dropped read, and only reports unknown for the former.

**The reload screen latched on "Wrong Card."** Tapping a fare card when the kiosk wanted the bank card showed an error that never cleared. Fix: auto-reset back to the prompt after ~2 seconds.

**Re-provisioning didn't reset a balance.** Running `provision.py` on an already-provisioned card returns `0xDE` — file already exists — and leaves the balance alone. Correct behavior, briefly confusing. Credit is the right way to add money, not re-creation.

**The reload cap was client-side only.** The log claimed `MAX_RELOAD=6000` was a server constant. It wasn't in the server at all. The browser blocked amounts over $60, and the JS checked for an `over_limit` error the server never sent — dead code. A direct POST sailed through. Fixed by enforcing it server-side.

**Then the server-side cap only checked the ceiling.** `if amount > MAX_RELOAD` — so a negative amount passed straight through to a signed credit operation. Fixed to `if amount <= 0 or amount > MAX_RELOAD`, with a second check at the line that actually moves money. Validating the ceiling and forgetting the floor is exactly what a reviewer looks for.

### Reliability

**Security made it slow.** The auth gate and the internal ID read roughly doubled the card operations per tap. In practice you hover a card for maybe half a second. The operations no longer fit in that window, so you'd get an approval followed by errors as you pulled the card away and the later reads failed. Multiple messages from one tap.

The instinct is to add retries. That's wrong — retries make the window problem worse. The fix was to **remove work**: one identify, one auth (needed anyway), read the ID while authenticated, one balance read, trust the debit response unless it looks wrong. Roughly half the round trips.

**One hover produced two actions.** Once the tap was lean enough, the scan loop cycled fast enough to catch the same card twice — charging, then instantly "transferring." Fix: separate detection from cooldown. A fast 0.2s poll keeps it sensitive; a 2.0s window after each tap silently discards reads. One hover, one action.

**Flaky reads after mounting everything in the shell.** Worked on the bench, degraded after assembly. Suspects were the LED harness detuning the reader's antenna, the reader's position shifting, or a marginal connection. Replugging wires changed the behavior, which pointed at contact quality. New jumpers improved it. The SPI pin map was verified correct.

**A red herring inside that:** I'd moved the PN532's power wire from pin 2 to pin 4. Both are the same 5V rail — electrically identical, zero effect. The real risk was that an SPI wire got shuffled during the move. It hadn't.

### Self-inflicted

These are the ones worth writing down, because they cost the most time and none of them were the machine's fault.

**Patch scripts fail silently.** Most edits were Python scripts doing find-and-replace on anchor strings. When an anchor doesn't match exactly, the replacement doesn't happen and nothing reports it. One of these left a variable used but never defined, and the reader stopped responding completely. The rule that came out of it: grep to verify a patch landed before relaunching.

**Grep for the feature, not your variable name.** I spent time "fixing" a red-on-zero-balance display that already worked, because the check searched for a variable name from a patch that never ran. The live code used a different name for the same thing. The feature had been there for days.

**A missing file looks exactly like a hardware bug.** After rewiring, one command read cards and another didn't. Textbook marginal connection. The wiring was fine — the second script had been lost in a rebuild weeks earlier and simply didn't exist. Check the file exists before you check the solder.

**Flask caches templates.** With `debug=False`, editing the HTML does nothing until the service restarts. A browser refresh isn't enough. Lost time chasing a fix that had applied correctly.

**Silence can be the correct output.** The UID watcher only prints when the UID *changes*. Tapping the static card looked like the script had frozen. It hadn't — a card that never changes its UID has nothing new to print. The silence was the proof. This confused me twice.

**Terminal freeze that wasn't.** Reaching for Ctrl+C and catching Ctrl+S locks terminal scrolling. The script keeps running, the output stops, and it looks identical to a hang. Ctrl+Q releases it.

---

## Cleanup and assembly

Two enclosures, both designed and printed on my own machine: one for the Pi, one for the reader. Both glued shut and aligned.

The layout is three zones on one stand. The reader sits up front — that's where you tap. The train is elevated in the middle, headlights facing forward. The Pi sits at the rear in its own enclosure. Tap point closest, the thing you're meant to look at in the center, compute out of the way.

The train is glued down.

**The boot service.** `omny.service` replaced the original `rfid.service` that had been conflicting over the SPI pins. It starts on power with `Restart=on-failure`. Plug the train in and the terminal comes up on its own: reader online, headlights running a self-test flash, web UI live. Confirmed by a reboot — the service came back on PID 671, well before any login.

**The hotspot.** If no known WiFi is available, the Pi brings up its own network. Tested by turning off the router: the hotspot appeared, connected from a laptop, the full stack worked at `10.42.0.1:5000`. It's one-way by design — once it commits to standalone it stays there until reboot. That's correct behavior for a demo. A stray network can't pull the machine off its own hotspot mid-tap.

**The backup.** The SD card is imaged with `dd` piped through `gzip`, then verified with `gzip -t`, which decompresses the whole archive to confirm it reads. 29 GiB down to 1.2 GB, exit code 0. An untested backup is a guess. The earlier text-only backups had captured the Python but never the systemd unit, the SPI overlay, or the hotspot config — the parts that make it an appliance rather than a script.

---

## Tradeoffs

Things I chose not to do, and why.

**The Gridfinity exoskeleton.** The original enclosure plan was a modular Gridfinity baseplate holding the Pi, the train, and the reader, with external clip-in wire channels and exposed wiring as a deliberate aesthetic. It was fully designed on paper. It got cut. A phone stand held everything at the right height, and the enclosure work that actually mattered was the two housings. It was a real design project that wouldn't have made the machine work any better.

**Random UID on one card only.** It's an irreversible one-way switch. Enabling it on the whole fleet would have been a permanent commitment for a feature I could demonstrate with one card. Keeping a static card as a control also makes the demo better — you see both behaviors side by side.

**The card master key stays factory default.** This is a real gap and it's deliberate. The master key is what authenticates `SetConfiguration`, and it's DES by default. Changing it would have meant rebuilding the random UID path against a new key type for no functional gain.

**Plain communication mode.** Fare operations aren't encrypted over the session — the auth gate proves the card is legitimate, but the read/debit/credit that follow run in plain mode. Full encrypted comm would mean CMAC-ing every value operation. It's the correct next step, and it's not done.

**No transaction counter.** Real EMV cards carry a monotonic counter for rollback protection. This doesn't. The reason it's defensible: writing anything to these cards requires my key. Rollback needs the key, and anyone with the key would just credit themselves directly. It's a real absence with a real justification, which is better than having built it without understanding why.

**The HMAC signing key lives in source.** In production it belongs in secure storage or an environment variable. Stated in the code comments.

**Transfer state is in memory.** Restart the service and the transfer windows reset. The money is safe — that's on the card — but the two-minute windows are lost. Acceptable for a demo, wrong for production.

**The rare "Unknown."** Roughly once in a while, a tap throws unknown on a card that's fine. It's a dropped frame in the auth handshake. Eliminating it means adding retries, which brings back the input lag that made the whole thing feel broken. In a real transit gate you'd tap again. Accepting a rare re-tap is the right call, not a defect.

**`topup.py` was superseded.** The reload kiosk does the same job with two-factor authorization. The standalone tool was lost in a rebuild and never rebuilt, because it shouldn't exist.

**The auth gate stops at access control.** There were two options: authenticate as a gate before operations (done), or run every operation inside an encrypted authenticated session (not done). The first is the meaningful security win. The second is a much larger rewrite for marginal demo benefit.

---

## Results

The full acceptance test, run in one sitting at the end.

**Crypto layer:**
- All three cards authenticate with my key; the factory default returns `0xAE` on both fare cards.
- The random-UID card produced seven consecutive different UIDs, all starting `08`. The static card stayed fixed.
- Both random UIDs (`0839aaf7`, `08c6ed21`) resolved to the same internal card ID — 1002. Same physical card, different UIDs, correct identification.
- 340-entry log chain intact.

**Live system:**
- Server-side limits: `-5000` rejected, `999999` rejected, `600` accepted — verified by raw request, bypassing the browser entirely.
- Two-factor reload with both indicators lighting independently.
- Wrong-card auto-reset confirmed working.
- Full terminal matrix: approve, transfer, re-charge after the cycle resets, insufficient, wrong card, and a foreign card rejected at the auth gate.
- The random-UID card rode and got a free transfer — identified across taps by internal ID.
- Headlights matched the screen on every tap.

**Tamper detection:**
- Forged a balance to $999.00 in the log. The verifier caught it: `Log INVALID after 4 entries: line 5: bad signature (tampered)`. Restored clean at 384 entries.

**Appliance:**
- Cold reboot to running service on PID 671, unattended, headlights self-testing.
- Router off: hotspot came up on its own, full stack worked with no infrastructure.

Every test passed.

**What it is now:** a self-contained encrypted payment terminal that owns its own cryptographic keys, identifies cards by authenticated internal data rather than a spoofable UID, refuses forged audit logs, enforces its limits server-side, rides a card that presents a different identity on every tap, boots itself on power, and hosts its own network when there isn't one.

Built June 15 to July 16, 2026. Around a job, a semester, three other projects, and a stretch of burnout in the middle.

---

*Evil Avenue · New York City*
**[evilavenue.com](https://evilavenue.com)**
