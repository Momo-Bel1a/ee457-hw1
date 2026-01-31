import urllib.request
import urllib.error
import time
import json
import socket
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

class ResponseHandler:
    """
    响应处理基类。
    在实际项目中，可以继承此类来实现具体的日志记录或数据持久化逻辑。
    """
    def on_success(self, url: str, status: int, body: bytes, latency_ms: float) -> None:
        pass

    def on_client_error(self, url: str, status: int, body: bytes) -> None:
        pass

    def on_server_error(self, url: str, status: int, body: bytes) -> None:
        pass

    def on_retry(self, url: str, attempt: int, delay_s: float, error: str) -> None:
        pass

    def on_max_retries(self, url: str, attempts: int) -> None:
        pass

    def on_timeout(self, url: str) -> None:
        pass

    def on_connection_error(self, url: str, error: str) -> None:
        pass

    def on_slow_response(self, url: str, latency_ms: float) -> None:
        pass


class LoggingResponseHandler(ResponseHandler):
    """
    具体的响应处理器：将所有事件记录到日志文件，并维护统计数据。
    """
    def __init__(self, log_file: str):
        self.log_file = log_file
        self.stats = {
            "total_urls": 0,
            "successful": 0,
            "failed": 0,
            "total_requests": 0,
            "retries": 0,
            "total_latency_ms": 0.0,
            "slow_responses": 0,
            "by_status": {},
            "by_error": {"timeout": 0, "connection": 0}
        }
        # ensure log file is empty
        with open(self.log_file, 'w') as f:
            pass

    def _log(self, event: str, url: str, extra: dict = None):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "url": url
        }
        if extra:
            log_entry.update(extra)
        
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(log_entry) + "\n")

    def on_success(self, url: str, status: int, body: bytes, latency_ms: float) -> None:
        self.stats["successful"] += 1
        self.stats["total_latency_ms"] += latency_ms
        status_str = str(status)
        self.stats["by_status"][status_str] = self.stats["by_status"].get(status_str, 0) + 1
        
        self._log("on_success", url, {"status": status, "latency_ms": latency_ms})
        if latency_ms > 1000: # assume slow response if latency > 1s
            self.on_slow_response(url, latency_ms)

    def on_client_error(self, url: str, status: int, body: bytes) -> None:
        self.stats["failed"] += 1
        status_str = str(status)
        self.stats["by_status"][status_str] = self.stats["by_status"].get(status_str, 0) + 1
        self._log("on_client_error", url, {"status": status})

    def on_server_error(self, url: str, status: int, body: bytes) -> None:
        # 如果重试后依然失败，这里会计入最终失败
        self._log("on_server_error", url, {"status": status})

    def on_retry(self, url: str, attempt: int, delay_s: float, error: str) -> None:
        self.stats["retries"] += 1
        self._log("on_retry", url, {"attempt": attempt, "delay_s": delay_s, "error": error})

    def on_max_retries(self, url: str, attempts: int) -> None:
        self.stats["failed"] += 1
        self._log("on_max_retries", url, {"attempts": attempts})

    def on_timeout(self, url: str) -> None:
        self.stats["by_error"]["timeout"] += 1
        self._log("on_timeout", url)

    def on_connection_error(self, url: str, error: str) -> None:
        self.stats["by_error"]["connection"] += 1
        self._log("on_connection_error", url, {"error": error})

    def on_slow_response(self, url: str, latency_ms: float) -> None:
        self.stats["slow_responses"] += 1
        self._log("on_slow_response", url, {"latency_ms": latency_ms})

    def get_summary(self) -> dict:
        avg_latency = 0
        if self.stats["successful"] > 0:
            avg_latency = self.stats["total_latency_ms"] / self.stats["successful"]
        
        return {
            "total_urls": self.stats["total_urls"],
            "successful": self.stats["successful"],
            "failed": self.stats["failed"],
            "total_requests": self.stats["total_requests"],
            "retries": self.stats["retries"],
            "avg_latency_ms": round(avg_latency, 2),
            "slow_responses": self.stats["slow_responses"],
            "by_status": self.stats["by_status"],
            "by_error": self.stats["by_error"]
        }


class RobustHTTPClient:
    def __init__(self, handler: ResponseHandler, max_retries: int = 3, base_delay: float = 1.0):
        self.handler = handler
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.timeout = 5.0 # default timeout 5 seconds

    def fetch(self, url: str) -> bool:
        """
        抓取单个 URL，包含重试逻辑。
        """
        attempt = 0
        while attempt <= self.max_retries:
            if isinstance(self.handler, LoggingResponseHandler):
                self.handler.stats["total_requests"] += 1
            
            start_time = time.perf_counter()
            try:
                # use urllib.request.urlopen
                with urllib.request.urlopen(url, timeout=self.timeout) as response:
                    status = response.getcode()
                    body = response.read()
                    latency_ms = (time.perf_counter() - start_time) * 1000
                    
                    if 200 <= status < 300:
                        self.handler.on_success(url, status, body, latency_ms)
                        return True
                    else:
                        # although urlopen usually raises an exception for non-2xx, just in case
                        self.handler.on_client_error(url, status, body)
                        return False

            except urllib.error.HTTPError as e:
                status = e.code
                body = e.read()
                
                # 4xx error: permanent error, no retry
                if 400 <= status < 500:
                    self.handler.on_client_error(url, status, body)
                    return False
                
                # 5xx error: transient error, try retry
                self.handler.on_server_error(url, status, body)
                if not self._handle_retry(url, attempt, str(e)):
                    return False
                
            except (urllib.error.URLError, socket.timeout, socket.error) as e:
                # timeout or network connection error: try retry
                error_msg = str(e)
                if "timed out" in error_msg.lower():
                    self.handler.on_timeout(url)
                else:
                    self.handler.on_connection_error(url, error_msg)
                
                if not self._handle_retry(url, attempt, error_msg):
                    return False
            
            attempt += 1
            
        return False

    def _handle_retry(self, url: str, attempt: int, error_msg: str) -> bool:
        """Handle internal retry logic."""
        if attempt < self.max_retries:
            delay = self.base_delay * (2 ** attempt)
            self.handler.on_retry(url, attempt + 1, delay, error_msg)
            time.sleep(delay)
            return True
        else:
            self.handler.on_max_retries(url, attempt + 1)
            return False

    def fetch_all(self, provider: Any) -> dict:
        """
        Fetch all URLs from the provider and execute them.
        """
        if isinstance(self.handler, LoggingResponseHandler):
            self.handler.stats["total_urls"] = provider.total()

        while (url := provider.next_url()) is not None:
            self.fetch(url)
            
        if isinstance(self.handler, LoggingResponseHandler):
            summary = self.handler.get_summary()

            with open("summary.json", "w") as f:
                json.dump(summary, f, indent=4)
            return summary
        return {}

if __name__ == "__main__":
    print("Robust HTTP Client is ready.")
