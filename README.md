# EE547 HW1

Homework 1 for EE547: three problems (q1, q2, q3).

## Repository structure

```
ee547-hw1-[username]/
├── q1/
│   ├── merge_worker.py
│   └── README.md
├── q2/
│   ├── http_client.py
│   ├── url_provider.py
│   └── README.md
├── q3/
│   ├── event_logger.py
│   └── README.md
└── README.md
```

- **q1** — MergeWorker: distributed merge of sorted lists (file-based message passing).
- **q2** — Robust HTTP client (`http_client.py`) and URL provider (`url_provider.py`): retries, timeouts, handler API.
- **q3** — Event logger: crash-tolerant, out-of-order packet logging with CRC32.

See each `q1/README.md`, `q2/README.md`, `q3/README.md` for details.
