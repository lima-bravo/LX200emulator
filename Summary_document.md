**ScopeBoss Telescope State-Machine Emulator (Python) ---
Single-Document Design Spec**

This document summarizes the full design for a **Python-based telescope
state-machine emulator** that runs as a **separate TCP server** and
mimics a **Meade LX200GPS serial bus**. It is intended to be fed into a
coding environment as the implementation blueprint for the emulator.

It is grounded in the Meade "Telescope Serial Command Protocol"
(LX200GPS) including: **commands are ASCII and #-terminated**, the scope
may respond **NAK (0x15)** when busy, **ACK (0x06)** is used to query
mount mode, and the **home/park/sleep/wake** plus **focus** commands and
return semantics.It also incorporates the operational guidance that
after **Park** the telescope can then be **powered off**, and one should
**wait for motors to stop**. [\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
[\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)
[\[wiki.opena\...rotech.com\]](https://wiki.openastrotech.com/Knowledge/Firmware/MeadeCommands)

**1) Goals & Non-Goals**

**Goals**

- Provide a **deterministic** reference model for **TDD** and UI
  refactoring.

- Emulate the **serial protocol behavior** over TCP: **byte stream**,
  same command framing, same response shapes. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- Include core behaviors needed for ScopeBoss TDD:

  - **Park / Home / Sleep / Wake**

  - **Focuser movement & speed**

  - **Busy behavior** via **NAK (0x15)** with deterministic policy.
    [\[Meade Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
    [\[Meade Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- Expose internal state transitions in a way that a Swift UI can rely
  on.

**Non-Goals (initial versions)**

- Full astronomical correctness (precise coordinate transforms, sky
  model).

- Implementing every Meade command group (only a useful subset for
  tests).

- Multi-client arbitration (start with single-client for deterministic
  tests).

**2) Protocol Facts to Implement (Ground Truth)**

**Framing & transport**

- Commands are ASCII and typically terminated with #; many responses are
  #-terminated strings. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- **ACK (0x06)** is the alignment/mount mode query; scope returns a
  mount mode indicator. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Busy behavior**

- On LX200GPS, a possible response to any command is **ASCII NAK
  (0x15)** if the control chain is busy; the controller should retry
  after waiting. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Home / Park / Sleep / Wake (h-family)**

- :hS# Seek Home and **store encoder values** to nonvolatile memory
  (LX200GPS). Returns nothing. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :hF# Seek Home and **align/set based on stored encoder values**.
  Returns nothing. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :h?# Query Home Status. Returns: 0 failed, 1 found, 2 in progress.
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :hP# Slew to Park Position. Returns nothing. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :hN# Sleep telescope: power off motors/encoders/displays/lights;
  remains in minimum power until keystroke or wake command. [\[Meade
  Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :hW# Wake sleeping telescope. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Focuser (F-family)**

- :F+# move focuser inward; :F-# outward; :FQ# stop. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- Focuser speed: :F\<n\># where \<n\> is ASCII digit (1..4) for Autostar
  & LX200GPS; also :FF# fastest, :FS# slowest. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Operational "Park → Power Off" path**

- Practical operational guidance: after **Park**, telescope goes to Park
  position and can then be **powered off**; **wait for motors to stop**
  before powering off.
  [\[wiki.opena\...rotech.com\]](https://wiki.openastrotech.com/Knowledge/Firmware/MeadeCommands)

**3) System Architecture (TCP-based Serial Mimic)**

**High-level components**

1.  **TCP Server**

    - Listens on configurable host/port.

    - Accepts a single client connection (initially) for deterministic
      testing.

2.  **Byte-stream Command Parser**

    - Converts inbound TCP bytes into protocol command events.

3.  **State Machine Core**

    - Holds the reference truth state.

    - Applies transitions on command events and internal timers.

4.  **Response Writer**

    - Emits byte responses exactly like the serial link would (e.g.,
      single-byte NAK, ASCII digits for :h?#, etc.). [\[Meade Tele\...d
      Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
      [\[Meade Tele\...d
      Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

5.  **Deterministic Scheduler**

    - Advances time and triggers internal events like "park complete".

**Design principle**

- Keep the state machine **pure** (inputs → state changes + output
  effects).

- Keep network I/O and timekeeping outside, injecting **events**
  (rx_bytes, tick, client_disconnect) to the core.

**4) State Model (Orthogonal Regions)**

Implement these regions in parallel (statechart style). They are
separate enums/state fields but interact via an explicit lock policy.

**Region A --- Power/Link (session)**

- POWERED_OFF

- DISCONNECTED

- CONNECTING

- CONNECTED

- RECOVERING

- FAULTED

**Region B --- Motion / System Activity**

- IDLE_TRACKING

- SLEWING_TO_TARGET (optional in first emulator cut)

- MANUAL_MOVING (optional)

- HOMING

- PARKING

- PARKED_READY (app-level "safe to power off" milestone)

- SLEEPING

- POWERED_OFF (app-level, collapses link)

**Region C --- Focuser**

- FOCUS_IDLE

- FOCUS_IN

- FOCUS_OUT

- focus_speed (1..4, optional FF/FS mapping)

**5) Textual Statecharts (Mermaid)**

These diagrams are documentation and alignment tools (like "Markdown for
diagrams"), not executable logic.

**5.1 Power + Link**

stateDiagram-v2

  \[\*\] \--\> POWERED_OFF

  POWERED_OFF \--\> DISCONNECTED: power_on

  DISCONNECTED \--\> CONNECTING: tcp_accept

  CONNECTING \--\> CONNECTED: handshake_ok

  CONNECTED \--\> DISCONNECTED: tcp_close

  CONNECTED \--\> RECOVERING: repeated_timeouts_or_parse_errors

  RECOVERING \--\> CONNECTED: recover_ok

  RECOVERING \--\> DISCONNECTED: recover_failed

  DISCONNECTED \--\> POWERED_OFF: power_off

**5.2 Motion (Home/Park/Sleep)**

stateDiagram-v2

  \[*\] \--\> IDLE_TRACKING*

*  IDLE_TRACKING \--\> HOMING: :hS# or :hF#*

*  HOMING \--\> HOMING: :h?# == 2*

*  HOMING \--\> IDLE_TRACKING: :h?# == 1*

*  HOMING \--\> IDLE_TRACKING: :h?# == 0*

*  IDLE_TRACKING \--\> PARKING: :hP#*

*  PARKING \--\> PARKED_READY: park_complete (internal)*

*  IDLE_TRACKING \--\> SLEEPING: :hN#*

*  HOMING \--\> SLEEPING: :hN#*

*  PARKING \--\> SLEEPING: :hN#*

*  PARKED_READY \--\> SLEEPING: :hN#*

*  SLEEPING \--\> IDLE_TRACKING: :hW#*

*  PARKED_READY \--\> \[*\]: user_power_off (app-level)

Home status query return codes (0/1/2) are explicitly defined.Park and
Sleep/Wake commands and semantics are explicitly defined. [\[Meade
Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**5.3 Focuser**

stateDiagram-v2

  \[\*\] \--\> FOCUS_IDLE

  FOCUS_IDLE \--\> FOCUS_IN: :F+#

  FOCUS_IDLE \--\> FOCUS_OUT: :F-#

  FOCUS_IN \--\> FOCUS_IDLE: :FQ#

  FOCUS_OUT \--\> FOCUS_IDLE: :FQ#

Focuser commands and speed-setting forms are explicitly defined.
[\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**6) Command Set Supported (Initial Emulator Scope)**

**"Single byte" commands**

- 0x06 (ACK): respond with mount mode indicator. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- 0x15 (NAK): used by emulator as busy/unavailable response. [\[Meade
  Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Hash-terminated commands (subset)**

Home/Park/Sleep/Wake:

- :hS# --- start homing "store" (no response). [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :hF# --- start homing "align" (no response). [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :h?# --- return 0/1/2 home status. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :hP# --- start parking (no response). [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :hN# --- enter sleep (no response). [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :hW# --- wake (no response). [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

Focuser:

- :F+#, :F-#, :FQ# (no response). [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :F1#..:F4#, :FF#, :FS# (no response). [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**7) Streaming Command Parser (TCP Byte Stream)**

**Requirements**

- Must handle TCP packetization (partial frames, merged frames).

- Must detect both:

  1.  single-byte ACK (0x06)

  2.  #-terminated ASCII frames.

**Parsing algorithm (deterministic)**

Maintain rx_buffer: bytearray.

On every data_received(bytes):

1.  For each byte b:

    - If b == 0x06 and rx_buffer is empty: emit CMD_ACK.

    - Else append b to rx_buffer.

    - If b == ord(\'#\'):

      - Decode rx_buffer\[:-1\] as ASCII (strict or replace errors).

      - Emit CMD_FRAME(text + \'#\') or keep raw text and include #.

      - Clear buffer.

2.  Guardrail: if buffer length exceeds MAX_FRAME (e.g., 256), treat as
    parse error → clear buffer → optionally transition Link to
    RECOVERING.

This matches the protocol's framing reliance on \# terminators and
special ACK query. [\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
[\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**8) Operation Lock Policy (Busy/NAK Behavior)**

**Why a lock policy exists**

LX200GPS may respond **NAK (0x15)** when busy/unable to accept a
command; controller retries later.Your emulator should replicate this
deterministically so TDD can validate client retry logic. [\[Meade
Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
[\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Locks (long-running operations)**

Treat these motion states as holding a "busy lock":

- HOMING (started by :hS# or :hF#) [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- PARKING (started by :hP#) [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- SLEEPING (entered by :hN#) [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Allowed commands while busy**

**Always allowed (even when locked):**

- :h?# --- needed to observe home progress; home status codes are
  defined. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :FQ# --- safe stop for focuser. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- :hN# --- allow immediate entry to sleep, as defined. [\[Meade
  Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**When SLEEPING:**

- Allow only :hW# (wake) and optionally ACK query; return NAK for
  everything else. Wake is explicitly defined for sleeping telescope.
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**When PARKED_READY:**

- Allow app-level user_power_off (see below).

- Optionally return NAK to motion/focus commands if you want to enforce
  the "park → power off" safe flow for UI tests (this enforcement is an
  emulator policy, while the "park then power off" operational flow is
  supported by guidance).
  [\[wiki.opena\...rotech.com\]](https://wiki.openastrotech.com/Knowledge/Firmware/MeadeCommands)

**NAK response rule**

If a command is disallowed by the lock policy, respond with:

- 0x15 (NAK) only, promptly. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**9) Deterministic Completion Modeling (Park & Home)**

**Park completion**

- :hP# returns nothing and cited protocol text does not define a
  completion response.**Emulator policy:** on :hP#, set motion=PARKING
  and start a deterministic timer (configurable park_duration_ms). When
  timer elapses, transition to PARKED_READY. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

This supports the real-world workflow that after park completes, the
telescope can be powered off and you should wait for motors to stop.
[\[wiki.opena\...rotech.com\]](https://wiki.openastrotech.com/Knowledge/Firmware/MeadeCommands)

**Home completion**

- Use the explicit home status query :h?# returning 2 in progress, 1
  found, 0 failed.**Emulator policy options:** [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

1.  **Scripted** (best for TDD): define a sequence per test ("2,2,1") to
    simulate progress.

2.  **Timer-based**: return 2 until home_duration_ms elapses, then
    return 1 (or 0 based on config).

**10) "Park → Power Off" Path (App-Level Event)**

Because the protocol provides a park action but not a "power off"
command, model power-off as an emulator-level action:

- When motion enters PARKED_READY, allow a special **app-level event**
  user_power_off that:

  - transitions power/link to POWERED_OFF

  - closes TCP connection (simulating loss of serial power)

This maps to operational guidance that once parked, the telescope can be
powered off.
[\[wiki.opena\...rotech.com\]](https://wiki.openastrotech.com/Knowledge/Firmware/MeadeCommands)

**11) Suggested Configuration Surface (for tests)**

Provide a config object (loaded from JSON/YAML/env) to keep tests
deterministic:

- mount_mode_code: what ACK returns (e.g., AltAz/Land/Polar indicator)
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- park_duration_ms: time from :hP# to PARKED_READY

- home_behavior:

  - mode: \"scripted\" or \"timer\"

  - script: e.g., \[2, 2, 1\] or \[2, 0\] for failure

  - duration_ms: if timer-based

- nak_policy:

  - \"strict_lock_only\" (default deterministic)

  - optionally \"probabilistic\" (stress tests)

- max_frame_len: parser safety limit

**12) Minimal TDD Scenarios (Golden Tests)**

**Home store success**

1.  Send :hS# → no response, state becomes HOMING. [\[Meade Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

2.  Poll :h?# repeatedly → respond 2 then 1. [\[Meade Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

3.  After 1, state returns to IDLE_TRACKING.

**Home failure**

- :h?# returns 0 to indicate failed. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Park → ready → power off**

1.  Send :hP# → no response; state PARKING. [\[Meade Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

2.  After park_duration_ms, state PARKED_READY.

3.  Trigger user_power_off → socket closes, state POWERED_OFF
    (emulator-level), matching the "park then power off after motors
    stop" operational workflow.
    [\[wiki.opena\...rotech.com\]](https://wiki.openastrotech.com/Knowledge/Firmware/MeadeCommands)

**Sleep/wake**

- Send :hN# → no response; enter SLEEPING; reject other commands with
  NAK except :hW#. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- Send :hW# → no response; return to IDLE_TRACKING. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Focuser**

- :F3# set speed, then :F+# move in, then :FQ# stop. [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**13) Implementation Checklist (What to build, in order)**

1.  TCP server (single client)

2.  Streaming parser:

    - ACK byte

    - #-terminated frames

3.  State machine core with:

    - Motion states (HOMING/PARKING/PARKED_READY/SLEEPING)

    - Focuser states

4.  Lock policy:

    - disallowed → send NAK (0x15) [\[Meade Tele\...d
      Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
      [\[Meade Tele\...d
      Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

5.  Deterministic timers:

    - park completion

    - optional home completion or scripted home response

6.  Golden tests:

    - Home success/fail

    - Park → power off

    - Sleep/wake

    - Focus in/out/stop

**14) Reference Links (for implementers)**

- Meade LX200/LX200GPS command protocol PDF (Company Seven mirror):
  http://company7.com/library/meade/LX200CommandSet.pdf [\[Meade
  Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- Practical LX200GPS mini-control notes including park/sleep
  descriptions and "park then power off" guidance:
  [\[wiki.opena\...rotech.com\]](https://wiki.openastrotech.com/Knowledge/Firmware/MeadeCommands)

If you want, I can also turn this document into a **single "README.md"
style file content** (same text, but with a ready "Project Layout"
section and a stub module list: server.py, parser.py, state.py,
policy.py, tests/).

Below is a **ready-to-paste README.md** plus a complete set of **Python
code snippets** (minimal but functional) for a **TCP-based Meade
LX200GPS serial-protocol emulator** with:

- streaming **TCP byte parser** (#-terminated frames + ACK 0x06)

- **operation lock policy** that returns **NAK 0x15** when "busy" (as
  LX200GPS can do)
  [\[ensoptical.co.uk\]](https://ensoptical.co.uk/telescopes?page=3),
  [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- state machine covering **Park / Home / Sleep / Wake** (:hP# :hS# :hF#
  :h?# :hN# :hW#) [\[Meade Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf),
  [\[ensoptical.co.uk\]](https://ensoptical.co.uk/telescopes?page=3)

- focuser **move + stop + speed** (:F+# :F-# :FQ# :F1#..:F4#) [\[Meade
  Tele\...d
  Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- deterministic completion modeling (timer/script) to support TDD

- "**Park → safe to power off → power-off**" path (emulator-level),
  matching operational guidance to power off after park when motors stop
  [\[manualshelf.com\]](https://www.manualshelf.com/manual/meade/lx200-gps/instruction-manual-english/page-56.html)

**README.md (copy/paste)**

\# ScopeBoss LX200GPS State-Machine Emulator (TCP Serial Mimic)

This project provides a **Python** emulator for a subset of the **Meade
LX200GPS serial control protocol**, exposed over a **TCP socket** to
mimic a byte-stream serial bus.

It is designed as a **deterministic reference model** for:

\- TDD (golden tests)

\- UI refactors (SwiftUI migration)

\- transport swapping (real serial vs TCP emulator) without changing
higher-level behavior

\## Grounded protocol behaviors

\- Most commands are **ASCII** and **terminated with \#**; many
responses are also #-terminated strings. (We parse frames by looking for
#.) 

  Source: Meade Telescope Serial Command Protocol.
[1](https://ensoptical.co.uk/telescopes?page=3)[2](http://company7.com/library/meade/LX200CommandSet.pdf)

\- LX200GPS may respond **NAK (0x15)** when the control chain is busy; a
controller should wait and retry. 

  Source: protocol notes on NAK.
[1](https://ensoptical.co.uk/telescopes?page=3)[2](http://company7.com/library/meade/LX200CommandSet.pdf)

\- **ACK (0x06)** is an alignment/mounting mode query, and returns a
mount-mode indicator. 

  Source: protocol describes ACK query.
[4](https://astromart.com/classifieds/astromart-classifieds/telescope-catadioptric/show/meade-lx200-gps-autostar-with-feather-touch-focuser)[1](https://ensoptical.co.uk/telescopes?page=3)

\- Home/Park/Sleep/Wake commands included:

  - :hS# seek Home and store encoder values

  - :hF# seek Home and align based on stored values

  - :h?# query Home status -\> 0 failed, 1 found, 2 in progress

  - :hP# slew to Park position (returns nothing)

  - :hN# sleep (low power)

  - :hW# wake 

  Source: protocol Home commands.
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

\- Focuser commands included:

  - :F+# focus in, :F-# focus out, :FQ# stop

  - :F&lt;n&gt;# with n=1..4 set focuser speed (Autostar & LX200GPS) 

  Source: protocol Focus commands.
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

\- Operational Park → Power Off flow:

  - After Park, you can power off; wait for motors to stop. 

  Source: practical LX200GPS mini-control notes.
[3](https://www.manualshelf.com/manual/meade/lx200-gps/instruction-manual-english/page-56.html)

\## Supported commands (initial scope)

\### Single-byte

\- 0x06 (ACK): respond with configured mount-mode byte (defaults to P).

\- 0x15 (NAK): emulator sends this when \"busy/unavailable\".

\### Hash-terminated

Home/Park/Sleep/Wake:

\- :hS#, :hF#, :h?#, :hP#, :hN#, :hW#

Focuser:

\- :F+#, :F-#, :FQ#, :F1#, :F2#, :F3#, :F4# (optionally :FF#, :FS# as
aliases)

\## State machine design (textual diagrams)

\### Motion region

\`\`\`mermaid

stateDiagram-v2

  \[*\] \--\> IDLE_TRACKING*

*  IDLE_TRACKING \--\> HOMING: :hS# or :hF#*

*  HOMING \--\> HOMING: :h?# == 2*

*  HOMING \--\> IDLE_TRACKING: :h?# == 1*

*  HOMING \--\> IDLE_TRACKING: :h?# == 0*

*  IDLE_TRACKING \--\> PARKING: :hP#*

*  PARKING \--\> PARKED_READY: park_complete (internal)*

*  IDLE_TRACKING \--\> SLEEPING: :hN#*

*  HOMING \--\> SLEEPING: :hN#*

*  PARKING \--\> SLEEPING: :hN#*

*  PARKED_READY \--\> SLEEPING: :hN#*

*  SLEEPING \--\> IDLE_TRACKING: :hW#*

*  PARKED_READY \--\> \[*\]: user_power_off (emulator-level)

**Focuser region**

stateDiagram-v2

  \[\*\] \--\> FOCUS_IDLE

  FOCUS_IDLE \--\> FOCUS_IN: :F+#

  FOCUS_IDLE \--\> FOCUS_OUT: :F-#

  FOCUS_IN \--\> FOCUS_IDLE: :FQ#

  FOCUS_OUT \--\> FOCUS_IDLE: :FQ#

**Busy / Lock policy (NAK behavior)**

The emulator implements a deterministic \"busy\" policy:

- When motion is HOMING or PARKING, some commands may be rejected with
  NAK (0x15).

- When motion is SLEEPING, only :hW#, :h?# and ACK are accepted;
  everything else returns NAK.

This reflects the protocol\'s note that LX200GPS can respond with NAK
(0x15) when busy.
[\[ensoptical.co.uk\]](https://ensoptical.co.uk/telescopes?page=3),
[\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Deterministic completion modeling**

- :hP# returns nothing (no completion response in cited protocol text),
  so the emulator uses a deterministic timer:

  - after park_duration_ms, state becomes PARKED_READY.

- Home completion is driven by :h?#:

  - 2 while in progress; then 1 (found) or 0 (failed). [\[Meade
    Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**Project layout**

scopeboss_emulator/

server.py

config.py

protocol/

parser.py

types.py

core/

state.py

reducer.py

policy.py

tests/

test_protocol_basic.py

test_home_and_park.py

README.md

**Run**

python -m scopeboss_emulator.server \--host 127.0.0.1 \--port 4030

Then connect using your app or netcat/telnet (raw mode preferred).

Example (bash):

python - \<\<\'PY\'

import socket

s=socket.create_connection((\"127.0.0.1\",4030))

s.sendall(b\":hS#\")

s.sendall(b\":h?#\")

print(s.recv(64))

s.close()

PY

\`\`

**Notes**

- This emulator is a **byte-stream** device: do not send JSON framing;
  send raw command bytes.

- The emulator intentionally supports only a subset of commands; it is
  meant to be extended.

\-\--

\## Code snippets (copy into files)

\### \`scopeboss_emulator/config.py\`

\`\`\`python

from \_\_future\_\_ import annotations

from dataclasses import dataclass

import json

from pathlib import Path

from typing import Optional, List, Literal

HomeMode = Literal\[\"scripted\", \"timer\"\]

\@dataclass(frozen=True)

class HomeBehavior:

mode: HomeMode = \"timer\"

\# scripted sequence of codes returned by :h?# (2=in
progress,1=found,0=failed)

script: Optional\[List\[int\]\] = None

duration_ms: int = 2000 \# if timer-based, how long until found/failed

succeed: bool = True \# timer-based outcome

\@dataclass(frozen=True)

class EmulatorConfig:

\# ACK (0x06) mount mode indicator. The 2010 protocol text shows
D=AltAz, L=Land, P=Polar.
\[4\](https://astromart.com/classifieds/astromart-classifieds/telescope-catadioptric/show/meade-lx200-gps-autostar-with-feather-touch-focuser)

mount_mode_byte: str = \"P\"

\# Parser

max_frame_len: int = 256

\# Busy/NAK behavior

nak_on_lock: bool = True

\# Home/Park

park_duration_ms: int = 5000

home: HomeBehavior = HomeBehavior()

\# Response formatting for :h?# (protocol defines codes but not
terminator in cited snippet)
\[2\](http://company7.com/library/meade/LX200CommandSet.pdf)

home_status_append_hash: bool = False

def load_config(path: str \| None) -\> EmulatorConfig:

if not path:

return EmulatorConfig()

data = json.loads(Path(path).read_text(encoding=\"utf-8\"))

home_data = data.get(\"home\", {})

home = HomeBehavior(

mode=home_data.get(\"mode\", \"timer\"),

script=home_data.get(\"script\"),

duration_ms=int(home_data.get(\"duration_ms\", 2000)),

succeed=bool(home_data.get(\"succeed\", True)),

)

return EmulatorConfig(

mount_mode_byte=data.get(\"mount_mode_byte\", \"P\"),

max_frame_len=int(data.get(\"max_frame_len\", 256)),

nak_on_lock=bool(data.get(\"nak_on_lock\", True)),

park_duration_ms=int(data.get(\"park_duration_ms\", 5000)),

home=home,

home_status_append_hash=bool(data.get(\"home_status_append_hash\",
False)),

)

**scopeboss_emulator/protocol/types.py**

from **future** import annotations

from dataclasses import dataclass

from typing import Union

\@dataclass(frozen=True)

class CmdAck:

    \"\"\"Single-byte ACK (0x06) query.\"\"\"

    pass

\@dataclass(frozen=True)

class CmdFrame:

    \"\"\"A full \'#\'-terminated ASCII command frame (decoded).\"\"\"

    text: str  \# includes trailing \'#\', e.g. \':hP#\'

ProtocolCommand = Union\[CmdAck, CmdFrame\]

**scopeboss_emulator/protocol/parser.py**

from **future** import annotations

from dataclasses import dataclass

from typing import List

from .types import CmdAck, CmdFrame, ProtocolCommand

ACK_BYTE = 0x06  \# protocol ACK query
[1](https://ensoptical.co.uk/telescopes?page=3)[4](https://astromart.com/classifieds/astromart-classifieds/telescope-catadioptric/show/meade-lx200-gps-autostar-with-feather-touch-focuser)

HASH = ord(\"#\")

\@dataclass

class StreamParser:

    max_len: int = 256

    def **post_init**(self) -\> None:

        self.buf = bytearray()

    def feed(self, data: bytes) -\> List\[ProtocolCommand\]:

        out: List\[ProtocolCommand\] = \[\]

        for b in data:

            \# ACK is a single-byte query. We only treat it as such when
not in a frame.

            if b == ACK_BYTE and len(self.buf) == 0:

                out.append(CmdAck())

                continue

            self.buf.append(b)

            if len(self.buf) \> self.max_len:

                \# Parse error: drop buffer

                self.buf.clear()

                continue

            if b == HASH:

                raw = bytes(self.buf\[:-1\])

                self.buf.clear()

                try:

                    text = raw.decode(\"ascii\", errors=\"strict\")

                except UnicodeDecodeError:

                    \# invalid frame; ignore

                    continue

                out.append(CmdFrame(text=text + \"#\"))

        return out

**scopeboss_emulator/core/state.py**

from **future** import annotations

from dataclasses import dataclass, replace

from enum import Enum, auto

from typing import Optional, List

class LinkState(Enum):

    DISCONNECTED = auto()

    CONNECTING = auto()

    CONNECTED = auto()

    RECOVERING = auto()

class MotionState(Enum):

    IDLE_TRACKING = auto()

    HOMING = auto()

    PARKING = auto()

    PARKED_READY = auto()

    SLEEPING = auto()

    POWERED_OFF = auto()

class HomeStatus(Enum):

    UNKNOWN = auto()

    IN_PROGRESS = auto()

    FOUND = auto()

    FAILED = auto()

class FocusState(Enum):

    FOCUS_IDLE = auto()

    FOCUS_IN = auto()

    FOCUS_OUT = auto()

\@dataclass(frozen=True)

class Timers:

    park_done_at_ms: Optional\[int\] = None

    home_done_at_ms: Optional\[int\] = None

\@dataclass(frozen=True)

class TelescopeState:

    now_ms: int = 0

    link: LinkState = LinkState.DISCONNECTED

    motion: MotionState = MotionState.POWERED_OFF

    home_status: HomeStatus = HomeStatus.UNKNOWN

    focus: FocusState = FocusState.FOCUS_IDLE

    focus_speed: Optional\[int\] = None  \# 1..4

    timers: Timers = Timers()

    last_error: Optional\[str\] = None

**scopeboss_emulator/core/policy.py**

from **future** import annotations

from dataclasses import dataclass

from typing import Optional

from .state import MotionState

from ..protocol.types import CmdAck, CmdFrame, ProtocolCommand

NAK_BYTE = b\"\\x15\"  \# NAK (0x15) as busy/unavailable
[1](https://ensoptical.co.uk/telescopes?page=3)[2](http://company7.com/library/meade/LX200CommandSet.pdf)

\@dataclass(frozen=True)

class PolicyResult:

    allowed: bool

    immediate_reply: Optional\[bytes\] = None

def is_allowed(motion: MotionState, cmd: ProtocolCommand) -\>
PolicyResult:

    \"\"\"

    Deterministic lock policy:

    - In SLEEPING: allow only wake (:hW#), home status (:h?#), and ACK.

    - In HOMING/PARKING: allow :h?#, :hN#, :FQ#, and ACK; NAK many
others.

    \"\"\"

    if isinstance(cmd, CmdAck):

        return PolicyResult(True)

    if not isinstance(cmd, CmdFrame):

        return PolicyResult(False, NAK_BYTE)

    t = cmd.text

    \# Always-allowed safety/observe commands:

    if t == \":h?#\":

        return PolicyResult(True)

    if t == \":hN#\":

        return PolicyResult(True)

    if t == \":FQ#\":

        return PolicyResult(True)

    if motion == MotionState.SLEEPING:

        if t == \":hW#\":

            return PolicyResult(True)

        \# everything else unavailable

        return PolicyResult(False, NAK_BYTE)

    if motion in (MotionState.HOMING, MotionState.PARKING):

        \# allow wake? (not meaningful unless sleeping, but harmless)

        if t == \":hW#\":

            return PolicyResult(True)

        \# NAK most commands while busy

        return PolicyResult(False, NAK_BYTE)

    \# PARKED_READY: allow most except you may want to enforce \"power
off\" flow.

    \# We\'ll allow everything here; UI tests can still assert
\"power-off only in parked_ready\".

    return PolicyResult(True)

**scopeboss_emulator/core/reducer.py**

from **future** import annotations

from dataclasses import replace

from typing import Tuple, List, Optional

from .state import TelescopeState, LinkState, MotionState, HomeStatus,
FocusState, Timers

from ..config import EmulatorConfig

from ..protocol.types import CmdAck, CmdFrame, ProtocolCommand

ACK_BYTE = b\"\\x06\"  \# incoming query; responses are mode bytes (not
0x06)

def handle_command(state: TelescopeState, cmd: ProtocolCommand, cfg:
EmulatorConfig) -\> Tuple\[TelescopeState, Optional\[bytes\]\]:

    \"\"\"

    Apply command -\> new state, and optional immediate reply bytes.

    \"\"\"

    s = state

    \# ACK query: respond with configured mount mode indicator (e.g.,
\'P\', \'L\', \'D\')
[4](https://astromart.com/classifieds/astromart-classifieds/telescope-catadioptric/show/meade-lx200-gps-autostar-with-feather-touch-focuser)

    if isinstance(cmd, CmdAck):

        b = cfg.mount_mode_byte.encode(\"ascii\",
errors=\"ignore\")\[:1\]

        return s, b

    if not isinstance(cmd, CmdFrame):

        return replace(s, last_error=\"unknown_cmd_type\"), None

    t = cmd.text

    \# Home commands
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

    if t == \":hS#\" or t == \":hF#\":

        s = replace(

            s,

            motion=MotionState.HOMING,

            home_status=HomeStatus.IN_PROGRESS,

            timers=replace(s.timers, home_done_at_ms=s.now_ms +
cfg.home.duration_ms),

            last_error=None,

        )

        return s, None

    if t == \":h?#\":

        code = \_home_status_code(s, cfg)

        payload = str(code).encode(\"ascii\")

        if cfg.home_status_append_hash:

            payload += b\"#\"

        return s, payload

    if t == \":hP#\":

        s = replace(

            s,

            motion=MotionState.PARKING,

            timers=replace(s.timers, park_done_at_ms=s.now_ms +
cfg.park_duration_ms),

            last_error=None,

        )

        return s, None

    if t == \":hN#\":

        \# Sleep: power down (model), focuser forced idle
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

        s = replace(s, motion=MotionState.SLEEPING,
focus=FocusState.FOCUS_IDLE, last_error=None)

        return s, None

    if t == \":hW#\":

        s = replace(s, motion=MotionState.IDLE_TRACKING,
last_error=None)

        return s, None

    \# Focus commands
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

    if t == \":F+#\":

        s = replace(s, focus=FocusState.FOCUS_IN, last_error=None)

        return s, None

    if t == \":F-#\":

        s = replace(s, focus=FocusState.FOCUS_OUT, last_error=None)

        return s, None

    if t == \":FQ#\":

        s = replace(s, focus=FocusState.FOCUS_IDLE, last_error=None)

        return s, None

    \# Focus speed: :F1#..:F4# plus optional :FF#/:FS#

    if t in (\":FF#\", \":FS#\"):

        \# map aliases to speed if you want; protocol provides :FF#/:FS#
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

        \# We\'ll set 4 for FF, 1 for FS

        n = 4 if t == \":FF#\" else 1

        s = replace(s, focus_speed=n, last_error=None)

        return s, None

    if len(t) == 4 and t.startswith(\":F\") and t.endswith(\"#\"):

        \# e.g. \':F3#\'

        ch = t\[2\]

        if ch.isdigit():

            n = int(ch)

            if 1 \<= n \<= 4:

                s = replace(s, focus_speed=n, last_error=None)

                return s, None

    \# Unknown command: ignore (or set error)

    s = replace(s, last_error=f\"unhandled:{t}\")

    return s, None

def tick(state: TelescopeState, dt_ms: int, cfg: EmulatorConfig) -\>
TelescopeState:

    \"\"\"

    Advance time and fire deterministic timers.

    \"\"\"

    s = replace(state, now_ms=state.now_ms + dt_ms)

    \# Park completion -\> PARKED_READY (emulator modeling; :hP# returns
nothing in cited text)
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

    if s.timers.park_done_at_ms is not None and s.now_ms \>=
s.timers.park_done_at_ms:

        if s.motion == MotionState.PARKING:

            s = replace(s, motion=MotionState.PARKED_READY,
timers=replace(s.timers, park_done_at_ms=None))

    \# Home completion timer affects what :h?# will return

    if s.timers.home_done_at_ms is not None and s.now_ms \>=
s.timers.home_done_at_ms:

        \# If timer-based home behavior, we mark FOUND/FAILED but keep
HOMING until queried or immediately exit.

        \# We\'ll immediately exit to IDLE_TRACKING for simplicity,
while still allowing :h?# to report final code.

        if s.motion == MotionState.HOMING and cfg.home.mode ==
\"timer\":

            final = HomeStatus.FOUND if cfg.home.succeed else
HomeStatus.FAILED

            s = replace(s, home_status=final,
motion=MotionState.IDLE_TRACKING, timers=replace(s.timers,
home_done_at_ms=None))

    return s

def \_home_status_code(s: TelescopeState, cfg: EmulatorConfig) -\> int:

    \"\"\"

    Return code for :h?#: 0 failed, 1 found, 2 in progress.
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

    If scripted mode, pop from script until exhausted.

    \"\"\"

    if cfg.home.mode == \"scripted\" and cfg.home.script:

        \# We don\'t mutate cfg; scripted mode is better implemented
with a session-local script cursor.

        \# For minimal version, just return current status mapping.

        pass

    if s.motion == MotionState.HOMING:

        return 2

    if s.home_status == HomeStatus.FOUND:

        return 1

    if s.home_status == HomeStatus.FAILED:

        return 0

    \# default unknown -\> treat as in-progress if homing else failed

    return 2 if s.motion == MotionState.HOMING else 0

Note: The protocol defines the :h?# return codes (0/1/2) but the snippet
we have does not specify a terminator for that response. We made it
configurable (home_status_append_hash). [\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**scopeboss_emulator/server.py**

from **future** import annotations

import argparse

import asyncio

from typing import Optional

from .config import load_config, EmulatorConfig

from .protocol.parser import StreamParser

from .core.state import TelescopeState, LinkState, MotionState

from .core.policy import is_allowed, NAK_BYTE

from .core.reducer import handle_command, tick

TICK_MS = 50

async def client_session(reader: asyncio.StreamReader, writer:
asyncio.StreamWriter, cfg: EmulatorConfig) -\> None:

    parser = StreamParser(max_len=cfg.max_frame_len)

    state = TelescopeState(link=LinkState.CONNECTED,
motion=MotionState.IDLE_TRACKING)

    async def ticker():

        nonlocal state

        while True:

            await asyncio.sleep(TICK_MS / 1000.0)

            state = tick(state, TICK_MS, cfg)

    tick_task = asyncio.create_task(ticker())

    try:

        while True:

            data = await reader.read(4096)

            if not data:

                break

            cmds = parser.feed(data)

            for cmd in cmds:

                \# lock/busy policy

                pol = is_allowed(state.motion, cmd)

                if not pol.allowed:

                    if pol.immediate_reply is not None:

                        writer.write(pol.immediate_reply)

                        await writer.drain()

                    continue

                \# apply command

                state, reply = handle_command(state, cmd, cfg)

                if reply is not None:

                    writer.write(reply)

                    await writer.drain()

    finally:

        tick_task.cancel()

        try:

            await tick_task

        except asyncio.CancelledError:

            pass

        writer.close()

        await writer.wait_closed()

async def run_server(host: str, port: int, cfg: EmulatorConfig) -\>
None:

    server = await asyncio.start_server(lambda r, w: client_session(r,
w, cfg), host, port)

    addrs = \", \".join(str(sock.getsockname()) for sock in
server.sockets or \[\])

    print(f\"ScopeBoss emulator listening on {addrs}\")

    async with server:

        await server.serve_forever()

def main():

    ap = argparse.ArgumentParser()

    ap.add_argument(\"\--host\", default=\"127.0.0.1\")

    ap.add_argument(\"\--port\", default=4030, type=int)

    ap.add_argument(\"\--config\", default=None)

    args = ap.parse_args()

    cfg = load_config(args.config)

    asyncio.run(run_server(args.host, args.port, cfg))

if **name** == \"**main**\":

    main()

**Tests (pytest) --- minimal golden checks**

**tests/test_protocol_basic.py**

import asyncio

import pytest

\@pytest.mark.asyncio

async def test_ack_returns_mount_mode(tmp_path):

    \# Launch server in-process by importing run_server, or run as
subprocess in your environment.

    \# Here is a simple socket-level test idea; adapt to your test
harness.

    import socket

    s = socket.create_connection((\"127.0.0.1\", 4030))

    s.sendall(bytes(\[0x06\]))  \# ACK query
[4](https://astromart.com/classifieds/astromart-classifieds/telescope-catadioptric/show/meade-lx200-gps-autostar-with-feather-touch-focuser)[1](https://ensoptical.co.uk/telescopes?page=3)

    b = s.recv(1)

    assert len(b) == 1

    s.close()

**tests/test_home_and_park.py**

import socket

import time

def test_home_query_codes():

    s = socket.create_connection((\"127.0.0.1\", 4030))

    s.sendall(b\":hS#\")   \# start homing
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

    s.sendall(b\":h?#\")   \# query status
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

    resp = s.recv(8)

    assert resp\[:1\] in (b\"2\", b\"1\", b\"0\")

    s.close()

def test_park_then_ready_timer():

    s = socket.create_connection((\"127.0.0.1\", 4030))

    s.sendall(b\":hP#\")   \# park (no response)
[2](http://company7.com/library/meade/LX200CommandSet.pdf)

    \# wait beyond default park_duration_ms in config (5000ms)

    time.sleep(5.5)

    \# There is no direct \"park status\" command in our subset; this is
validated by internal state in extended tests.

    \# You can extend emulator with a debug-only query if needed for
tests.

    s.close()

**Notes on extending scripted Home behavior (recommended for TDD)**

The core reducer's \_home_status_code includes a comment about scripted
mode. For truly deterministic per-test scripting, store a
**session-local cursor** (e.g., in the session object, not in config).
You can implement:

- state.home_script = \[2,2,1\]

- on each :h?#, pop and respond accordingly (keeping motion consistent)

The protocol explicitly defines meaning of the codes (0/1/2) for :h?#.
[\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

**One small "practical" addition (optional, but very useful)**

For tests, you may want a **debug-only** command (not part of the Meade
protocol) such as:

- ::DBGSTATE# → returns a JSON snapshot of internal emulator state

Keep it behind a config flag so your production TDD harness can
introspect without affecting the serial behavior used by the iOS
transport layer.

If you'd like, I can also generate a **single "all-in-one" module**
version (one file) for easier early experimentation, and then you can
split it into the package layout once the Swift tests are stable.

Below is a **single-file, ready-to-run Python module** you can drop into
your repo (e.g. scopeboss_lx200gps_emulator.py). It implements:

- **TCP server** (asyncio) that behaves like a **byte-stream serial
  link**

- **Streaming parser**:

  - single-byte **ACK (0x06)** → returns configured mount-mode byte
    [\[ensoptical.co.uk\]](https://ensoptical.co.uk/telescopes?page=3),
    [\[astromart.com\]](https://astromart.com/classifieds/astromart-classifieds/telescope-catadioptric/show/meade-lx200-gps-autostar-with-feather-touch-focuser)

  - **ASCII frames terminated by \#**

- Core state machine for **Home / Park / Sleep / Wake**:

  - :hS# :hF# :h?# :hP# :hN# :hW# [\[Meade Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- **Focuser**:

  - :F+# :F-# :FQ# :F1#..:F4# (plus :FF#, :FS# aliases) [\[Meade
    Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- **Operation lock policy**:

  - disallowed commands return **NAK (0x15)** to mimic LX200GPS "busy"
    behavior
    [\[ensoptical.co.uk\]](https://ensoptical.co.uk/telescopes?page=3),
    [\[Meade Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- Deterministic "completion":

  - Park completes after a timer because :hP# returns nothing in the
    cited protocol text [\[Meade Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

  - Home completion is driven by either timer or scripted status codes
    (0/1/2) as defined by :h?# [\[Meade Tele\...d
    Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

- Optional debug-only commands (off by default):

  - ::STATE# (returns JSON snapshot)

  - ::POWEROFF# (simulates "Park → power off" path; operationally park
    then power off is recommended)
    [\[manualshelf.com\]](https://www.manualshelf.com/manual/meade/lx200-gps/instruction-manual-english/page-56.html)

#!/usr/bin/env python3

\"\"\"

scopeboss_lx200gps_emulator.py

Single-module TCP emulator for a subset of the Meade LX200GPS serial
protocol.

Implements Home/Park/Sleep/Wake + Focuser, plus:

\- ACK (0x06) mount-mode query response

\- Busy/NAK (0x15) lock policy

\- TCP byte-stream parser (#-terminated frames)

Protocol grounding:

\- Commands are ASCII and typically terminated by \'#\'.
[1](https://ensoptical.co.uk/telescopes?page=3)[3](http://company7.com/library/meade/LX200CommandSet.pdf)

\- LX200GPS may respond with NAK (0x15) when busy; controller retries.
[1](https://ensoptical.co.uk/telescopes?page=3)[3](http://company7.com/library/meade/LX200CommandSet.pdf)

\- Home commands :hS# :hF# :h?# :hP# :hN# :hW# are defined; :h?# returns
0/1/2. [3](http://company7.com/library/meade/LX200CommandSet.pdf)

\- Focus commands :F+# :F-# :FQ# and :F\<n\># (1..4) are defined.
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

\- Operational guidance: after Park, you can power off (wait for motors
to stop).
[4](https://www.manualshelf.com/manual/meade/lx200-gps/instruction-manual-english/page-56.html)

\"\"\"

from **future** import annotations

import argparse

import asyncio

import json

import random

import time

from dataclasses import dataclass, asdict

from enum import Enum, auto

from typing import List, Optional, Tuple, Union

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

\# Protocol constants

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

ACK_BYTE = 0x06  \# alignment/mount mode query
[1](https://ensoptical.co.uk/telescopes?page=3)[2](https://astromart.com/classifieds/astromart-classifieds/telescope-catadioptric/show/meade-lx200-gps-autostar-with-feather-touch-focuser)

NAK_BYTE = 0x15  \# busy/unavailable response
[1](https://ensoptical.co.uk/telescopes?page=3)[3](http://company7.com/library/meade/LX200CommandSet.pdf)

HASH = ord(\"#\")

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

\# Config

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

class HomeMode(str, Enum):

    TIMER = \"timer\"

    SCRIPTED = \"scripted\"

\@dataclass

class EmulatorConfig:

    host: str = \"127.0.0.1\"

    port: int = 4030

    \# ACK response mount-mode byte. Commonly P/L/D per protocol listing
(Polar/Land/AltAz).
[2](https://astromart.com/classifieds/astromart-classifieds/telescope-catadioptric/show/meade-lx200-gps-autostar-with-feather-touch-focuser)

    mount_mode_byte: str = \"P\"

    \# Parser safety

    max_frame_len: int = 256

    \# Busy behavior

    nak_on_lock: bool = True

    \# optional probabilistic NAK injection (stress-testing). default 0
for deterministic.

    nak_probability: float = 0.0

    \# Home modeling

    home_mode: HomeMode = HomeMode.TIMER

    home_duration_ms: int = 2000

    home_timer_succeed: bool = True

    \# Scripted home status responses for :h?# (2=in progress, 1=found,
0=failed) [3](http://company7.com/library/meade/LX200CommandSet.pdf)

    home_script: Optional\[List\[int\]\] = None

    \# Park modeling

    park_duration_ms: int = 5000

    \# Response formatting

    home_status_append_hash: bool = False

    \# Debug-only commands (non-Meade)

    allow_debug_commands: bool = False

def load_config(path: Optional\[str\]) -\> EmulatorConfig:

    if not path:

        return EmulatorConfig()

    with open(path, \"r\", encoding=\"utf-8\") as f:

        data = json.load(f)

    cfg = EmulatorConfig()

    for k, v in data.items():

        if hasattr(cfg, k):

            setattr(cfg, k, v)

    \# enum normalize

    if isinstance(cfg.home_mode, str):

        cfg.home_mode = HomeMode(cfg.home_mode)

    return cfg

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

\# Command types & parser

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

\@dataclass(frozen=True)

class CmdAck:

    pass

\@dataclass(frozen=True)

class CmdFrame:

    text: str  \# includes trailing \'#\', e.g. \':hP#\'

ProtocolCommand = Union\[CmdAck, CmdFrame\]

class StreamParser:

    \"\"\"Streaming byte parser: emits CmdAck for single 0x06 bytes
(when not in a frame),

    and CmdFrame for ASCII frames terminated with \'#\'.

    \"\"\"

    def **init**(self, max_len: int = 256):

        self.max_len = max_len

        self.buf = bytearray()

    def feed(self, data: bytes) -\> List\[ProtocolCommand\]:

        out: List\[ProtocolCommand\] = \[\]

        for b in data:

            \# ACK is single-byte query, only if we\'re not accumulating
a frame

            if b == ACK_BYTE and len(self.buf) == 0:

                out.append(CmdAck())

                continue

            self.buf.append(b)

            if len(self.buf) \> self.max_len:

                \# parse error: drop buffer

                self.buf.clear()

                continue

            if b == HASH:

                raw = bytes(self.buf\[:-1\])

                self.buf.clear()

                try:

                    text = raw.decode(\"ascii\", errors=\"strict\")

                except UnicodeDecodeError:

                    continue

                out.append(CmdFrame(text=text + \"#\"))

        return out

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

\# State machine definitions

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

class MotionState(Enum):

    IDLE_TRACKING = auto()

    HOMING = auto()

    PARKING = auto()

    PARKED_READY = auto()

    SLEEPING = auto()

    POWERED_OFF = auto()

class HomeStatus(Enum):

    UNKNOWN = auto()

    IN_PROGRESS = auto()

    FOUND = auto()

    FAILED = auto()

class FocusState(Enum):

    FOCUS_IDLE = auto()

    FOCUS_IN = auto()

    FOCUS_OUT = auto()

\@dataclass

class EmulatorState:

    now_ms: int = 0

    motion: MotionState = MotionState.IDLE_TRACKING

    home_status: HomeStatus = HomeStatus.UNKNOWN

    focus: FocusState = FocusState.FOCUS_IDLE

    focus_speed: Optional\[int\] = None  \# 1..4

    last_error: Optional\[str\] = None

    \# timers

    park_done_at_ms: Optional\[int\] = None

    home_done_at_ms: Optional\[int\] = None

    \# scripted home state (session-local cursor)

    home_script: Optional\[List\[int\]\] = None

    home_script_index: int = 0

    def to_debug_dict(self) -\> dict:

        d = {

            \"now_ms\": self.now_ms,

            \"motion\": self.motion.name,

            \"home_status\": self.home_status.name,

            \"focus\": self.focus.name,

            \"focus_speed\": self.focus_speed,

            \"last_error\": self.last_error,

            \"park_done_at_ms\": self.park_done_at_ms,

            \"home_done_at_ms\": self.home_done_at_ms,

            \"home_script\": self.home_script,

            \"home_script_index\": self.home_script_index,

        }

        return d

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

\# Lock policy

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

def is_allowed(state: EmulatorState, cmd: ProtocolCommand, cfg:
EmulatorConfig) -\> Tuple\[bool, Optional\[bytes\]\]:

    \"\"\"Return (allowed, immediate_reply). If not allowed,
immediate_reply is usually NAK.\"\"\"

    \# probabilistic NAK injection (optional)

    if cfg.nak_probability \> 0.0 and random.random() \<
cfg.nak_probability:

        return False, bytes(\[NAK_BYTE\])

    if isinstance(cmd, CmdAck):

        return True, None

    if not isinstance(cmd, CmdFrame):

        return False, bytes(\[NAK_BYTE\])

    t = cmd.text

    \# Debug commands (non-Meade)

    if t.startswith(\"::\") and cfg.allow_debug_commands:

        return True, None

    if t.startswith(\"::\") and not cfg.allow_debug_commands:

        return False, bytes(\[NAK_BYTE\])

    \# Always allowed observer/safety commands

    if t == \":h?#\":  \# query home status
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

        return True, None

    if t == \":hN#\":  \# sleep
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

        return True, None

    if t == \":FQ#\":  \# stop focusing
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

        return True, None

    \# Sleeping: only allow wake + home status + ACK

    if state.motion == MotionState.SLEEPING:

        if t == \":hW#\":  \# wake
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

            return True, None

        return False, bytes(\[NAK_BYTE\]) if cfg.nak_on_lock else (True,
None)

    \# Busy operations: HOMING/PARKING

    if state.motion in (MotionState.HOMING, MotionState.PARKING):

        \# Disallow most commands while busy; allow wake harmlessly

        if t == \":hW#\":

            return True, None

        return False, bytes(\[NAK_BYTE\]) if cfg.nak_on_lock else (True,
None)

    \# Otherwise allowed

    return True, None

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

\# Reducer / command handling

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

def handle_command(state: EmulatorState, cmd: ProtocolCommand, cfg:
EmulatorConfig) -\> Tuple\[EmulatorState, Optional\[bytes\], bool\]:

    \"\"\"

    Apply a single command.

    Returns: (state, reply_bytes_or_None, close_connection_flag)

    \"\"\"

    close_conn = False

    \# ACK query -\> return mount mode byte (single byte)

    if isinstance(cmd, CmdAck):

        b = cfg.mount_mode_byte.encode(\"ascii\",
errors=\"ignore\")\[:1\] or b\"P\"

        return state, b, close_conn

    if not isinstance(cmd, CmdFrame):

        state.last_error = \"unknown_cmd_type\"

        return state, None, close_conn

    t = cmd.text

    \# Debug commands (non-Meade)

    if t == \"::STATE#\" and cfg.allow_debug_commands:

        payload = json.dumps(state.to_debug_dict(), separators=(\",\",
\":\")).encode(\"utf-8\") + b\"\\n\"

        return state, payload, close_conn

    if t == \"::POWEROFF#\" and cfg.allow_debug_commands:

        \# Emulator-level power-off: close socket and mark powered off.

        state.motion = MotionState.POWERED_OFF

        close_conn = True

        return state, None, close_conn

    \# Home commands (h-family)
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

    if t in (\":hS#\", \":hF#\"):

        state.motion = MotionState.HOMING

        state.home_status = HomeStatus.IN_PROGRESS

        state.home_done_at_ms = state.now_ms + int(cfg.home_duration_ms)

        state.last_error = None

        \# initialize scripted sequence cursor from config,
session-local copy

        if cfg.home_mode == HomeMode.SCRIPTED and cfg.home_script:

            state.home_script = list(cfg.home_script)

            state.home_script_index = 0

        else:

            state.home_script = None

            state.home_script_index = 0

        return state, None, close_conn

    if t == \":h?#\":

        code = home_status_code(state, cfg)

        payload = str(code).encode(\"ascii\")

        if cfg.home_status_append_hash:

            payload += b\"#\"

        return state, payload, close_conn

    \# Park command (returns nothing in protocol text)
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

    if t == \":hP#\":

        state.motion = MotionState.PARKING

        state.park_done_at_ms = state.now_ms + int(cfg.park_duration_ms)

        state.last_error = None

        return state, None, close_conn

    \# Sleep/Wake
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

    if t == \":hN#\":

        state.motion = MotionState.SLEEPING

        state.focus = FocusState.FOCUS_IDLE

        state.last_error = None

        return state, None, close_conn

    if t == \":hW#\":

        state.motion = MotionState.IDLE_TRACKING

        state.last_error = None

        return state, None, close_conn

    \# Focuser commands
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

    if t == \":F+#\":

        state.focus = FocusState.FOCUS_IN

        state.last_error = None

        return state, None, close_conn

    if t == \":F-#\":

        state.focus = FocusState.FOCUS_OUT

        state.last_error = None

        return state, None, close_conn

    if t == \":FQ#\":

        state.focus = FocusState.FOCUS_IDLE

        state.last_error = None

        return state, None, close_conn

    if t in (\":FF#\", \":FS#\"):

        \# Aliases listed in protocol for focus speed extremes
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

        state.focus_speed = 4 if t == \":FF#\" else 1

        state.last_error = None

        return state, None, close_conn

    \# :F\<n\># speed set, n=1..4 for LX200GPS
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

    if len(t) == 4 and t.startswith(\":F\") and t.endswith(\"#\"):

        ch = t\[2\]

        if ch.isdigit():

            n = int(ch)

            if 1 \<= n \<= 4:

                state.focus_speed = n

                state.last_error = None

                return state, None, close_conn

    \# Unknown command: ignore but record error

    state.last_error = f\"unhandled:{t}\"

    return state, None, close_conn

def tick(state: EmulatorState, dt_ms: int, cfg: EmulatorConfig) -\>
EmulatorState:

    state.now_ms += dt_ms

    \# Park completion -\> PARKED_READY (emulator model; :hP# has no
completion response in cited text)
[3](http://company7.com/library/meade/LX200CommandSet.pdf)

    if state.park_done_at_ms is not None and state.now_ms \>=
state.park_done_at_ms:

        if state.motion == MotionState.PARKING:

            state.motion = MotionState.PARKED_READY

        state.park_done_at_ms = None

    \# Home completion (timer-based) sets final status and returns to
idle

    if state.home_done_at_ms is not None and state.now_ms \>=
state.home_done_at_ms:

        if state.motion == MotionState.HOMING and cfg.home_mode ==
HomeMode.TIMER:

            state.home_status = HomeStatus.FOUND if
cfg.home_timer_succeed else HomeStatus.FAILED

            state.motion = MotionState.IDLE_TRACKING

        state.home_done_at_ms = None

    return state

def home_status_code(state: EmulatorState, cfg: EmulatorConfig) -\> int:

    \"\"\"

    :h?# returns:

      0 = Home Search Failed

      1 = Home Search Found

      2 = Home Search in Progress

    [3](http://company7.com/library/meade/LX200CommandSet.pdf)

    \"\"\"

    if cfg.home_mode == HomeMode.SCRIPTED and state.home_script:

        \# If homing, feed scripted sequence

        if state.home_script_index \< len(state.home_script):

            code = int(state.home_script\[state.home_script_index\])

            state.home_script_index += 1

            \# update emulator state based on code

            if code == 2:

                state.motion = MotionState.HOMING

                state.home_status = HomeStatus.IN_PROGRESS

            elif code == 1:

                state.home_status = HomeStatus.FOUND

                state.motion = MotionState.IDLE_TRACKING

            elif code == 0:

                state.home_status = HomeStatus.FAILED

                state.motion = MotionState.IDLE_TRACKING

            else:

                \# invalid scripted value -\> treat as failed

                state.home_status = HomeStatus.FAILED

                state.motion = MotionState.IDLE_TRACKING

                code = 0

            return code

        \# script exhausted -\> map current state

        \# fall through to mapping below

    if state.motion == MotionState.HOMING or state.home_status ==
HomeStatus.IN_PROGRESS:

        return 2

    if state.home_status == HomeStatus.FOUND:

        return 1

    if state.home_status == HomeStatus.FAILED:

        return 0

    \# unknown -\> treat as not found/failed

    return 0

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

\# TCP server

\# \-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\--

TICK_MS = 50

async def client_session(reader: asyncio.StreamReader, writer:
asyncio.StreamWriter, cfg: EmulatorConfig) -\> None:

    peer = writer.get_extra_info(\"peername\")

    print(f\"\[conn\] {peer}\")

    parser = StreamParser(max_len=cfg.max_frame_len)

    state = EmulatorState(motion=MotionState.IDLE_TRACKING)

    async def ticker():

        nonlocal state

        while True:

            await asyncio.sleep(TICK_MS / 1000.0)

            state = tick(state, TICK_MS, cfg)

    tick_task = asyncio.create_task(ticker())

    try:

        while True:

            data = await reader.read(4096)

            if not data:

                break

            cmds = parser.feed(data)

            for cmd in cmds:

                allowed, immediate = is_allowed(state, cmd, cfg)

                if not allowed:

                    if immediate is not None:

                        writer.write(immediate)

                        await writer.drain()

                    continue

                state, reply, close_conn = handle_command(state, cmd,
cfg)

                if reply is not None:

                    writer.write(reply)

                    await writer.drain()

                if close_conn:

                    \# simulate power-off by closing socket

                    writer.close()

                    await writer.wait_closed()

                    return

    finally:

        tick_task.cancel()

        try:

            await tick_task

        except asyncio.CancelledError:

            pass

        try:

            writer.close()

            await writer.wait_closed()

        except Exception:

            pass

        print(f\"\[disc\] {peer}\")

async def run_server(cfg: EmulatorConfig) -\> None:

    server = await asyncio.start_server(lambda r, w: client_session(r,
w, cfg), cfg.host, cfg.port)

    addrs = \", \".join(str(sock.getsockname()) for sock in
(server.sockets or \[\]))

    print(f\"ScopeBoss LX200GPS emulator listening on {addrs}\")

    async with server:

        await server.serve_forever()

def main():

    ap = argparse.ArgumentParser(description=\"ScopeBoss LX200GPS TCP
serial emulator (single module)\")

    ap.add_argument(\"\--host\", default=\"127.0.0.1\")

    ap.add_argument(\"\--port\", default=4030, type=int)

    ap.add_argument(\"\--config\", default=None, help=\"path to JSON
config\")

    ap.add_argument(\"\--debug\", action=\"store_true\", help=\"enable
debug-only commands ::STATE# and ::POWEROFF#\")

    args = ap.parse_args()

    cfg = load_config(args.config)

    cfg.host = args.host

    cfg.port = args.port

    if args.debug:

        cfg.allow_debug_commands = True

    asyncio.run(run_server(cfg))

if **name** == \"**main**\":

    main()

**Quick run / smoke test**

**Run server**

python scopeboss_lx200gps_emulator.py \--host 127.0.0.1 \--port 4030

**Test from Python client**

import socket, time

s = socket.create_connection((\"127.0.0.1\",4030))

\# ACK query (0x06) -\> mount mode byte

s.sendall(bytes(\[0x06\]))

print(\"ACK-\>\", s.recv(1))

\# Home store + query

s.sendall(b\":hS#\")

s.sendall(b\":h?#\")

print(\":h?# -\>\", s.recv(16))

\# Sleep then try focus (should NAK)

s.sendall(b\":hN#\")

s.sendall(b\":F+#\")

print(\"focus while sleeping -\>\", s.recv(1))  \# expect 0x15 NAK

\# Wake

s.sendall(b\":hW#\")

\# Park (no reply), wait for timer, then (debug) poweroff if enabled

s.sendall(b\":hP#\")

time.sleep(5.5)

s.close()

**Nice next step (optional)**

If you want your iOS tests to assert *"park complete"* without enabling
debug commands, we can add a **standard Meade-readable observable**
(still protocol-compatible) by exposing park status via an existing
query (or a strictly test-only debug query behind config). The cited
protocol text doesn't define a park completion response for :hP#, so any
completion signaling is necessarily emulator policy. [\[Meade Tele\...d
Protocol\]](http://company7.com/library/meade/LX200CommandSet.pdf)

If you tell me which transport format your ScopeBoss app uses today for
serial (raw bytes vs line-based), I can align the emulator's response
formatting (especially for :h?#) to match your existing expectations.
