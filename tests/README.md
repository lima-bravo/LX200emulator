# Tests

## Layout

- **`conftest.py`** — Shared fixtures: `fast_cfg` (short park/home timers for speed), `idle_state`.
- **`unit/`** — Self-contained unit tests; no TCP or server.
  - `test_parser.py` — StreamParser: frames, ACK, chunked input, overflow, invalid ASCII.
  - `test_policy.py` — `is_allowed`: NAK when busy, allowed commands per motion state.
  - `test_reducer.py` — `handle_command` and `tick`: state transitions and reply bytes.
- **`test_protocol_basic.py`**, **`test_home_and_park.py`** — Legacy integration-style tests (require server on port 4030).

## Run

From project root:

```bash
uv run pytest tests/unit -v          # all unit tests
uv run pytest tests/unit -m unit -v   # same (unit marker)
uv run pytest tests/ -v               # all tests (unit + legacy; legacy needs server)
```

Unit tests use `fast_cfg` (e.g. 100 ms park/home) so timer-based assertions complete without sleep.
