# LX200emulator

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
