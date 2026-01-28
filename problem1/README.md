# MergeWorker Implementation Guide

This document provides a detailed explanation of the `merge_worker.py` implementation, which implements a distributed merge algorithm using a **Range-based approach** for optimizing sorted list merging between two workers.

## Overview

The `MergeWorker` class implements a single worker that can operate as either Worker A or Worker B. It uses file-based message passing to communicate with its partner and implements an optimized merge algorithm that leverages range information to minimize comparisons.

## Core Data Structures

### Message
```python
@dataclass
class Message:
    msg_type: str   # Max 5 chars: "META", "DATA", or "DONE"
    values: list[int]  # Max 10 integers
```

**Message Types:**
- `META`: Contains metadata `[min, max, count]` - 3 integers describing the worker's data range
- `DATA`: Contains a chunk of actual data values (up to 10 integers)
- `DONE`: Signals completion (empty values list)

### WorkerStats
```python
@dataclass
class WorkerStats:
    comparisons: int      # Number of comparison operations
    messages_sent: int    # Number of messages written
    messages_received: int # Number of messages read
    values_output: int    # Number of values written to output
```

## State Machine

The worker operates in three distinct phases:

### Phase 1: INIT
**Purpose:** Exchange metadata to determine range relationships

**State Variables:**
- `my_min`, `my_max`, `my_count`: Own data metadata
- `partner_min`, `partner_max`, `partner_count`: Partner's metadata (initially None)
- `metadata_sent`, `metadata_received`: Flags for metadata exchange

**Actions:**
- Send own metadata: `[min, max, count]`
- Receive partner metadata
- Transition to MERGE phase when both metadata exchanged

### Phase 2: MERGE
**Purpose:** Exchange data and perform optimized merging

**State Variables:**
- `data_index`: Current position for sending data chunks
- `merge_my_index`: Current position in own data for merging
- `merge_other_index`: Current position in received data for merging
- `other_data`: Buffer storing received data chunks
- `data_sent`, `data_received`: Flags for data exchange completion
- `output_count`: Total number of values output so far
- `safe_output_index`: Index for range-based safe output (currently unused but reserved)

**Key Strategy:** Range-based optimization determines three cases:

#### Case 1: Disjoint Ranges (my_max < partner_min)
**Optimization:** No comparisons needed!

- Worker A: Sends all data to Worker B
- Worker B: 
  1. Outputs all own values first (0 comparisons)
  2. Then outputs all received partner values (0 comparisons)
- **Result:** Zero comparison operations

#### Case 2: Disjoint Ranges (my_min > partner_max)
**Optimization:** No comparisons needed!

- Worker A: Sends all data to Worker B
- Worker B:
  1. Outputs all received partner values first (0 comparisons)
  2. Then outputs all own values (0 comparisons)
- **Result:** Zero comparison operations

#### Case 3: Overlapping Ranges
**Standard merge required:**

- Both workers send data chunks (up to 10 values per message)
- Worker B performs two-pointer merge:
  - Compare `data[merge_my_index]` with `other_data[merge_other_index]`
  - Output the smaller value
  - Advance the corresponding index
  - Continue until both lists exhausted
- **Result:** Requires comparison operations (O(n+m) comparisons)

### Phase 3: DONE
**Purpose:** Verify completion and terminate

**Actions:**
- Worker B double-checks all values have been output
- If incomplete, returns to MERGE phase
- Otherwise, saves final state and returns `False` (work complete)

## Key Methods

### `__init__(worker_id, data, inbox, outbox, output, state_file)`
Initializes the worker with:
- `worker_id`: "A" or "B" to distinguish workers
- `data`: Sorted list of integers this worker holds
- `inbox`: Path to read messages from partner
- `outbox`: Path to write messages to partner
- `output`: Path to append merged results
- `state_file`: Path to persist state between steps

### `step() -> bool`
**Core execution method** - called repeatedly until merge completes.

**Execution Flow:**
1. **Read Messages:** Check inbox for new messages (META, DATA, or DONE)
2. **Phase-Specific Actions:**
   - INIT: Send/receive metadata
   - MERGE: Execute range-based merge strategy
   - DONE: Verify completion
3. **Output Values:** Worker B outputs finalized values (up to 10 per step)
4. **Save State:** Persist state to file for recovery
5. **Return:** `True` if more work, `False` if done

**Key Implementation Details:**
- Each step outputs at most 10 values (chunking for efficiency)
- Only Worker B outputs final merged results
- State is saved after every step for fault tolerance

### `_read_message() -> Message | None`
Reads and parses JSON message from inbox file.
- Returns `None` if file doesn't exist or parse fails
- Updates `messages_received` statistic
- Handles JSON parsing errors gracefully

### `_write_message(message: Message)`
Writes message to outbox file as JSON.
- Serializes Message dataclass to JSON
- Updates `messages_sent` statistic
- Overwrites previous message (only one message per step)

### `_append_output(values: list[int])`
Appends values to output file, one per line.
- Only called by Worker B
- Updates `values_output` statistic
- Appends mode to support incremental output

### `_load_state() -> dict`
Loads persisted state from JSON file.
- Returns saved state if file exists
- Otherwise calls `_initial_state()` for first run

### `_save_state()`
Saves current state to JSON file.
- Called after every step for persistence
- Enables recovery from interruptions

## Range-Based Algorithm Details

### Range Relationship Detection

```python
ranges_disjoint_before = (my_max < partner_min)  # Case 1
ranges_disjoint_after = (my_min > partner_max)    # Case 2
# Otherwise: ranges overlap (Case 3)
```

### Optimization Benefits

**For Non-Overlapping Ranges:**
- **Zero comparisons** required
- Direct sequential output
- Minimal message overhead (only metadata + data transfer)

**For Overlapping Ranges:**
- Standard merge algorithm
- O(n+m) comparisons in worst case
- Efficient chunked processing (10 values per step)

### Example Scenarios

**Scenario 1: Non-Overlapping (Case 1)**
```
Worker A: [1000, 1001, ..., 5999]  (max=5999)
Worker B: [0, 1, ..., 999]          (min=0, max=999)
Result: B outputs [0-999], then [1000-5999] with 0 comparisons
```

**Scenario 2: Non-Overlapping (Case 2)**
```
Worker A: [0, 1, ..., 999]          (max=999)
Worker B: [1000, 1001, ..., 5999]  (min=1000)
Result: B outputs [0-999], then [1000-5999] with 0 comparisons
```

**Scenario 3: Overlapping**
```
Worker A: [0, 2, 4, 6, 8, ...]      (even numbers)
Worker B: [1, 3, 5, 7, 9, ...]      (odd numbers)
Result: Standard merge with comparisons needed
```

## State Persistence

The worker maintains state in a JSON file to support:
- **Fault Tolerance:** Can resume after interruption
- **Incremental Execution:** Each step is independent
- **Debugging:** State can be inspected between steps

**State Structure:**
```json
{
  "phase": "INIT|MERGE|DONE",
  "my_min": int,
  "my_max": int,
  "my_count": int,
  "partner_min": int | null,
  "partner_max": int | null,
  "partner_count": int | null,
  "data_index": int,
  "merge_my_index": int,
  "merge_other_index": int,
  "other_data": [int, ...],
  "data_sent": bool,
  "data_received": bool,
  "metadata_sent": bool,
  "metadata_received": bool,
  "output_count": int,
  "safe_output_index": int
}
```

## Output Strategy

**Important:** Only Worker B outputs the final merged result.

**Rationale:**
- Worker B (typically with less data) receives data from Worker A
- Worker B performs the merge and outputs results
- Worker A only sends data, doesn't output
- This avoids duplicate outputs and ensures single source of truth

**Output Format:**
- One integer per line
- Appended incrementally (up to 10 values per step)
- Final file contains complete merged sorted list

## Constraints and Limitations

1. **Message Size:** Maximum 10 integers per DATA message
2. **Message Type:** Limited to 5 characters ("META", "DATA", "DONE")
3. **One Message Per Step:** Each step writes at most one message
4. **Output Chunking:** Maximum 10 values output per step
5. **File-Based Communication:** Workers can only read inbox and write outbox

## Statistics Tracking

The worker tracks four key metrics:
- **comparisons:** Incremented during merge operations (Case 3 only)
- **messages_sent:** Count of messages written to outbox
- **messages_received:** Count of messages read from inbox
- **values_output:** Count of values written to output file

These statistics help analyze algorithm performance and communication overhead.

## Error Handling

The implementation handles:
- Missing message files (returns None)
- JSON parsing errors (returns None)
- Empty data lists (handles None values)
- State file corruption (reinitializes state)

## Performance Characteristics

**Best Case (Non-Overlapping Ranges):**
- Time Complexity: O(n + m) for data transfer
- Comparisons: 0
- Messages: ~(n+m)/10 + 2 (metadata + DONE)

**Worst Case (Fully Overlapping Ranges):**
- Time Complexity: O(n + m) for merge
- Comparisons: O(n + m)
- Messages: ~(n+m)/10 + 2

**Space Complexity:**
- O(n + m) for storing received data
- O(1) for state variables

## Design Decisions

1. **Range-Based Optimization:** Chosen over bubble-style for better performance on non-overlapping ranges
2. **Single Output Worker:** Worker B outputs to avoid duplication
3. **Chunked Processing:** 10 values per step balances efficiency and granularity
4. **State Persistence:** Enables fault tolerance and debugging
5. **Incremental Output:** Supports streaming large results
