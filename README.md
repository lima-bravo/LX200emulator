# LX200emulator

>>>>>>> 1eb0d59336aba89db7ea34d053159cc330bb8bc8

This python utility emulates an LX200GPS connected to a SkiFi device. It is intended to help speed development of LX200 apps for iOS and Android.

This utility is very incomplete and will extend over time, as more features are needed.

Usage: `python lx200emulator.py <port>`

The default port is 4030.


## Meade LX200 Serial Protocol

The PDF content is already fully in my context, so I can parse it directly without reading from disk. Let me build the JSON now. Here's the full protocol parsed into a structured JSON. A few notes on how it's organized that should help your emulator:

**Top-level structure:**

- `protocol`, `revision`, `date` — metadata
- `notes` — encoding conventions: the `#` terminator, precision mode formats, and the `x`/`p`/`-` support codes
- `commands` — keyed by command group designator (e.g. `"G"`, `"M"`, `"$Q"`)

**Per-command fields:**

- `cmd` — the exact command string to match (e.g. `":GR#"`, `":MS#"`)
- `description` — human-readable explanation
- `params` — argument formats where applicable
- `returns` — either a plain format string, or a dict of response-code → meaning for multi-value responses
- `support` — per-model support flags (`x`/`p`/`-`) where the document specifies them

**Emulator tips:**

- Commands returning `"Nothing"` should produce no serial output
- Multi-case returns (like `:MS#` returning `0`, `1<string>#`, or `2<string>#`) are modelled as dicts so you can branch on them
- The `precision_modes` note is important — many `G`/`S` commands have two response formats depending on the `:U#` toggle state you need to track
- The `support` field lets you easily filter which commands your LX200 emulator model should respond to vs. silently ignore

# ScopeBoss LX200GPS State-Machine Emulator (TCP Serial Mimic)  (New version)

This project provides a **Python** emulator for a subset of the **Meade LX200GPS serial control protocol**, exposed over a **TCP socket** to mimic a byte-stream serial bus.

It is designed as a **deterministic reference model** for:

- TDD (golden tests)
- UI refactors (SwiftUI migration)
- transport swapping (real serial vs TCP emulator) without changing higher-level behavior

## Grounded protocol behaviors

- Most commands are **ASCII** and **terminated with `#`**; many responses are also `#`-terminated strings. (We parse frames by looking for `#`.)  
  Source: Meade Telescope Serial Command Protocol. 12

- LX200GPS may respond **NAK (0x15)** when the control chain is busy; a controller should wait and retry.  
  Source: protocol notes on NAK. 12

- **ACK (0x06)** is an alignment/mounting mode query, and returns a mount-mode indicator.  
  Source: protocol describes ACK query. 41

- Home/Park/Sleep/Wake commands included:
  - `:hS#` seek Home and store encoder values
  - `:hF#` seek Home and align based on stored values
  - `:h?#` query Home status -> `0` failed, `1` found, `2` in progress
  - `:hP#` slew to Park position (returns nothing)
  - `:hN#` sleep (low power)
  - `:hW#` wake  
  Source: protocol Home commands. 2

- Focuser commands included:
  - `:F+#` focus in, `:F-#` focus out, `:FQ#` stop
  - `:F<n>#` with n=1..4 set focuser speed (Autostar & LX200GPS)  
  Source: protocol Focus commands. 2

- Operational Park → Power Off flow:
  - After `Park`, you can power off; wait for motors to stop.  
  Source: practical LX200GPS mini-control notes. 3

## Supported commands (initial scope)

### Single-byte

- `0x06` (ACK): respond with configured mount-mode byte (defaults to `P`).
- `0x15` (NAK): emulator sends this when "busy/unavailable".

### Hash-terminated

Home/Park/Sleep/Wake:

- `:hS#`, `:hF#`, `:h?#`, `:hP#`, `:hN#`, `:hW#`

Focuser:

- `:F+#`, `:F-#`, `:FQ#`, `:F1#`, `:F2#`, `:F3#`, `:F4#` (optionally `:FF#`, `:FS#` as aliases)

## Network–serial bridge (net2serial_bridge)

`net2serial_bridge.py` listens on a TCP port and forwards all traffic to/from a serial port (e.g. `/dev/ttyUSB0`). Optionally logs every read/write with Unix timestamp (millisecond), `R` (read from serial) or `W` (write to serial), and the raw ASCII message.

```bash
python net2serial_bridge.py [--host 0.0.0.0] [--port 4030] [--serial /dev/ttyUSB0] [--log path.log] [--baud 9600]
```

- `--log FILE` — append lines to FILE; use `-` for stdout.
- Log line format: `{unix_ts_ms}:{R|W}:{raw_ascii_message}`

## State machine design (textual diagrams)

### Motion region

```mermaid
stateDiagram-v2
  [*] --> IDLE_TRACKING

  IDLE_TRACKING --> HOMING: :hS# or :hF#
  HOMING --> HOMING: :h?# == 2
  HOMING --> IDLE_TRACKING: :h?# == 1
  HOMING --> IDLE_TRACKING: :h?# == 0

  IDLE_TRACKING --> PARKING: :hP#
  PARKING --> PARKED_READY: park_complete (internal)

  IDLE_TRACKING --> SLEEPING: :hN#
  HOMING --> SLEEPING: :hN#
  PARKING --> SLEEPING: :hN#
  PARKED_READY --> SLEEPING: :hN#

  SLEEPING --> IDLE_TRACKING: :hW#

  PARKED_READY --> [*]: user_power_off (emulator-level)

