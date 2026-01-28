# Robust HTTP Client

A Python HTTP client with retry logic, configurable timeouts, and a pluggable response-handler API. It fetches URLs via `urllib.request`, handles 4xx/5xx and network errors, and supports logging and statistics through a handler interface.

## Components

### 1. `ResponseHandler` (Base Class)

Abstract base class for handling fetch outcomes. Subclass it to implement logging, persistence, or custom logic. All methods are no-ops by default.

| Method | When Called |
|--------|-------------|
| `on_success(url, status, body, latency_ms)` | Response status in `[200, 300)` |
| `on_client_error(url, status, body)` | Status in `[400, 500)` (no retries) |
| `on_server_error(url, status, body)` | Status in `[500, 600)` before retry |
| `on_retry(url, attempt, delay_s, error)` | Before sleeping and retrying |
| `on_max_retries(url, attempts)` | Retries exhausted |
| `on_timeout(url)` | Request timed out |
| `on_connection_error(url, error)` | Connection/network error |
| `on_slow_response(url, latency_ms)` | Optional; e.g. when latency &gt; threshold |

### 2. `LoggingResponseHandler`

Concrete handler that:

- Appends JSON log lines to a file (one object per line: `timestamp`, `event`, `url`, plus event-specific fields).
- Keeps in-memory stats: successes, failures, retries, latencies, status-code and error-type counts.
- Treats responses slower than 1000 ms as slow and logs them via `on_slow_response`.
- Exposes `get_summary()` for aggregates (including `avg_latency_ms`, `by_status`, `by_error`).

**Constructor:** `LoggingResponseHandler(log_file: str)`

**Stats fields:** `total_urls`, `successful`, `failed`, `total_requests`, `retries`, `total_latency_ms`, `slow_responses`, `by_status`, `by_error` (e.g. `timeout`, `connection`).

### 3. `RobustHTTPClient`

HTTP client that performs fetches and drives the handler callbacks.

**Constructor:**

```python
RobustHTTPClient(handler: ResponseHandler, max_retries: int = 3, base_delay: float = 1.0)
```

- **handler** — Receives all events (success, errors, retries, timeout, etc.).
- **max_retries** — Max retry count per URL (e.g. 3 → up to 4 attempts total).
- **base_delay** — Base backoff in seconds; delay per retry is `base_delay * (2 ** attempt)`.

**Instance default:** `timeout = 5.0` seconds per request (not currently exposed in the constructor).

**Methods:**

- **`fetch(url: str) -> bool`**  
  Fetches one URL with retries.  
  - 2xx → `on_success`, returns `True`.  
  - 4xx → `on_client_error`, returns `False` (no retry).  
  - 5xx → `on_server_error`, then retry with backoff; after max retries → `on_max_retries`, returns `False`.  
  - Timeout / connection errors → `on_timeout` / `on_connection_error`, then retry; after max retries → `on_max_retries`, returns `False`.

- **`fetch_all(provider) -> dict`**  
  Consumes all URLs from a provider:  
  - Calls `provider.total()` and uses it as `total_urls` if the handler is `LoggingResponseHandler`.  
  - In a loop, calls `provider.next_url()` until it returns `None`, and calls `fetch(url)` for each.  
  - If the handler is `LoggingResponseHandler`, writes `get_summary()` to `summary.json` and returns that summary; otherwise returns `{}`.

## URLProvider Contract

The client expects a provider object used with `fetch_all()` to support:

- **`total() -> int`** — Total number of URLs (used for stats when using `LoggingResponseHandler`).
- **`next_url() -> str | None`** — Next URL to fetch, or `None` when done.

Optional: `get_behavior(url)` and `remaining()` for tests or validation; the client does not call them.

## Usage Example

```python
from http_client import RobustHTTPClient, LoggingResponseHandler
from url_provider import URLProvider

handler = LoggingResponseHandler("fetch.log")
client = RobustHTTPClient(handler, max_retries=3, base_delay=1.0)

provider = URLProvider(seed=42)
summary = client.fetch_all(provider)

# summary (and summary.json) include:
# total_urls, successful, failed, total_requests, retries,
# avg_latency_ms, slow_responses, by_status, by_error
```

## Dependencies

- Python 3 standard library: `urllib.request`, `urllib.error`, `time`, `json`, `socket`, `datetime`.

No extra packages are required.

## Log and Output Files

- **Log file** — Path given to `LoggingResponseHandler`. Each line is a JSON object with `timestamp`, `event`, `url`, and event-specific fields.
- **`summary.json`** — Written by `fetch_all()` when the handler is `LoggingResponseHandler` (in the current working directory). Contains the summary returned by `get_summary()`.

## Retry and Backoff

- Only 5xx and transport errors (timeout, connection) are retried.
- 4xx responses are reported via `on_client_error` and never retried.
- Backoff: delay before attempt `k` is `base_delay * (2 ** k)` seconds.
- After the last attempt, the client calls `on_max_retries` and stops.
