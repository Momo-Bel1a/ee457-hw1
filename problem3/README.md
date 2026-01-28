# Event Logger (Problem 3)

A crash-tolerant event logger that consumes out-of-order packets from a `MessageSource`, validates them with CRC32, deduplicates by sequence number, and writes them to a log file in sequence order. State is persisted after every packet so that a process crash (e.g. `sys.exit(1)`) can be recovered by resuming from the last saved state.

## Components

### 1. `Packet` (dataclass)

Represents a single event/packet.

| Field       | Type  | Description                          |
|------------|-------|--------------------------------------|
| `sequence` | int   | Monotonic sequence number            |
| `timestamp`| float | Event time                           |
| `payload`  | bytes | Raw payload bytes                    |
| `checksum` | int   | CRC32 of `payload` (e.g. `zlib.crc32(payload)`) |

**Methods:**

- **`to_dict()`** — Returns a JSON-serializable dict; `payload` is stored as hex string.
- **`from_dict(d)`** — Class method to build a `Packet` from a dict; `d['payload']` must be a hex string.

### 2. `EventLogger`

Persists packets to a log file in sequence order and keeps recoverable state in a separate state file.

**Constructor:** `EventLogger(log_path: Path, state_path: Path)`

- **log_path** — Append-only log file; one JSON object per line.
- **state_path** — JSON file holding `last_logged_seq`, `stats`, and serialized `buffer`. Restored on init if it exists.

**Internal state:**

- `last_logged_seq` — Sequence number of the last packet written to the log (-1 when none).
- `buffer` — Dict `{seq: Packet}` for packets that have arrived out of order and not yet written.
- `stats` — Counters: `received`, `corrupted`, `duplicates`, `written`.

**Public methods:**

- **`log_packet(packet: Packet)`**  
  1. Increments `received`.  
  2. Verifies `zlib.crc32(packet.payload) == packet.checksum`; on failure increments `corrupted`, saves state, returns.  
  3. Rejects duplicates: `sequence <= last_logged_seq` or already in `buffer` → increment `duplicates`, save state, return.  
  4. Inserts the packet into `buffer`, calls `_drain_buffer()` to write any new contiguous suffix, then `_save_state()`.

- **`force_flush(max_gap: int = 10)`**  
  Gap-based flush: if the smallest sequence in `buffer` is more than `max_gap` beyond `last_logged_seq`, treats the gap as lost, advances `last_logged_seq` to just before that minimum, drains the buffer, and saves state. Use this periodically (e.g. every N packets) to avoid unbounded buffering when packets are missing.

- **`get_stats()`**  
  Returns the current `stats` dict.

**Persistence:**

- **State file** — Written after every `log_packet` and after `force_flush`. Implemented as write-to-temp then `os.replace` for atomic updates.
- **Log file** — Each written packet is appended as one line:  
  `{"seq": <int>, "ts": <float>, "data": "<payload hex>"}`

### 3. MessageSource contract

The logger is driven by a source that provides packets. The test script expects a `MessageSource` with:

- **`MessageSource(seed=...)`** — Constructor; `seed` is used for deterministic behavior.
- **`remaining() -> int`** — Number of packets not yet returned by `next_packet()`.
- **`next_packet() -> dict | Packet | None`** — Next packet, or `None` when finished. If a dict, it must have `sequence`, `timestamp`, `payload` (hex string for `Packet.from_dict`), and `checksum`.

The assignment may supply its own `message_source` module; a mock is included in this folder for local runs when that module is absent.

## Usage

```python
from pathlib import Path
from event_logger import EventLogger, Packet
from message_source import MessageSource  # or assignment-provided module

log_file = Path("event.log")
state_file = Path("logger_state.json")
logger = EventLogger(log_file, state_file)
source = MessageSource(seed=42)

while (data := source.next_packet()) is not None:
    pkt = Packet.from_dict(data) if isinstance(data, dict) else data
    logger.log_packet(pkt)
    if logger.stats["received"] % 100 == 0:
        logger.force_flush(max_gap=15)

print(logger.get_stats())  # received, corrupted, duplicates, written
```

## Running the test

From the `problem3` directory:

```bash
python3 test_event_logger.py
```

The script pulls packets from `MessageSource(seed=42)`, passes them through `EventLogger`, and prints final stats. If `message_source` is missing, it exits with an error; ensure `message_source.py` is in the same directory (or replace it with the assignment’s version).

## Dependencies

Standard library only: `json`, `os`, `zlib`, `pathlib`, `dataclasses`, `typing`. No extra packages required.

## File formats

**Log file (e.g. `event.log`):**  
One JSON object per line, UTF-8:

```json
{"seq": 0, "ts": 1234567890.1, "data": "a1b2c3d4e5f6..."}
{"seq": 1, "ts": 1234567890.2, "data": "..."}
```

**State file (e.g. `logger_state.json`):**  
Single JSON object:

```json
{
  "last_logged_seq": 5,
  "stats": {"received": 10, "corrupted": 0, "duplicates": 1, "written": 6},
  "buffer": {"7": {"sequence": 7, "timestamp": ..., "payload": "...", "checksum": ...}}
}
```

After a crash, re-running the test (or your own loop) will reload this state and continue from the next sequence; already-written packets are not re-written thanks to `last_logged_seq` and duplicate checks.
