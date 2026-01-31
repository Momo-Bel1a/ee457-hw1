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

---

## Buffer Size Choice and Reasoning

We use a **single in-memory buffer** implemented as a dict: `buffer: Dict[int, Packet]` keyed by sequence number. There is **no fixed buffer size** (no fixed number of slots or bytes).

- **Reasoning:** Packets can arrive out of order; we must hold any packet that is not yet the next expected one (`last_logged_seq + 1`) until we can form a contiguous run to write. A fixed small buffer could force us to drop packets that are merely late, which would break correctness. An unbounded-by-count buffer keeps all out-of-order packets until they are written or declared lost via gap handling.
- **Trade-off:** Under heavy loss or extreme reordering, the buffer could grow large. We mitigate this with **gap handling** (see below) so that after a gap we stop waiting for missing sequences and drain what we have.

---

## Flush Strategy (When Do We Write?)

We write to the log in two situations:

1. **On every packet (drain):** After inserting a new packet into the buffer, we call `_drain_buffer()`. This writes **all consecutive** packets starting from `last_logged_seq + 1` that are currently in the buffer. So we write **as soon as** we have the next expected sequence (and any following consecutive ones). No periodic timer; writing is driven by packet arrival and contiguity.
2. **Periodic gap flush:** The caller (e.g. test script) is expected to call `force_flush(max_gap)` periodically (e.g. every 100 received packets). That does not write by itself on a schedule—it only runs when invoked. When it runs, it may **advance the watermark** and then drain (see gap handling below).

So: we write whenever we have new contiguous data (drain after each packet), and we use periodic `force_flush` to handle gaps so we don’t wait forever for lost packets.

---

## Gap Handling Approach

If we only drained when we had `last_logged_seq + 1`, a single lost packet would block all later packets forever. So we use **gap detection** in `force_flush(max_gap)`:

- If the **smallest** sequence number in the buffer is greater than `last_logged_seq + max_gap`, we treat the range `(last_logged_seq, min_buffered_seq)` as **lost**.
- We set `last_logged_seq = min_buffered_seq - 1` (so the next expected sequence becomes `min_buffered_seq`), then call `_drain_buffer()` to write all consecutive packets we can, and save state.

So we **skip** missing sequences after a gap of more than `max_gap` (e.g. 10 or 15), and continue writing. This keeps the log ordered by sequence while allowing progress under packet loss.

---

## Trade-offs Observed During Testing

- **Saving state after every packet:** Ensures we can recover from a crash after any packet, but increases disk writes. Trade-off: crash tolerance vs. write volume. We chose tolerance.
- **Unbounded buffer:** Avoids dropping late-but-valid packets, but under severe loss the buffer can grow. Gap handling limits how long we wait; we accept possible memory growth in exchange for correctness and simplicity.
- **Gap threshold `max_gap`:** A small `max_gap` (e.g. 10) flushes gaps quickly and keeps buffer smaller but may declare packets lost too early if they are just slow. A larger `max_gap` (e.g. 15) waits longer for late packets but may hold more in memory. In testing, 10–15 worked well for the given traffic.
- **Write-on-contiguity only:** We never write non-contiguous sequences; gap handling effectively “skips” missing seqs. So the log stays strictly ordered by sequence, but we may have gaps in sequence numbers (missing packets). That is an intentional trade-off: ordered, recoverable log vs. completeness under loss.
