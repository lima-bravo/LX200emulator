# LX200emulator (Original version)


This python utility emulates an LX200GPS connected to a SkiFi device. It is intended to help speed development of LX200 apps for iOS and Android.

This utility is very incomplete and will extend over time, as more features are needed.

Usage:  python lx200emulator.py <port>

The default port is 4030.



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