"""
URL Provider for HTTP Client Testing

Generates URLs to httpbin.org with known behaviors for testing
retry logic, error handling, and response classification.

Provided to students - do not modify.
"""

import random
from dataclasses import dataclass
from typing import Optional


# Default number of URLs to generate
DEFAULT_URL_COUNT = 50


@dataclass
class URLBehavior:
    """Describes expected behavior for a URL."""
    status_code: Optional[int]  # Expected status, None if timeout/connection error
    min_latency_ms: float       # Minimum expected response time
    should_retry: bool          # Whether client should retry on failure
    expected_callbacks: list[str]  # Callbacks that should fire
    body_keywords: list[str]    # Keywords expected in response body
    error_type: Optional[str]   # "timeout", "connection", or None


class URLProvider:
    """
    Generates test URLs with known behaviors.

    Uses httpbin.org endpoints to produce predictable responses:
    - /get: Fast 200 response
    - /delay/N: Slow response (N seconds)
    - /status/CODE: Returns specific status code
    - /html: Returns HTML content with predictable body
    """

    def __init__(self, seed: Optional[int] = None, count: int = DEFAULT_URL_COUNT):
        """
        Initialize URL provider.

        Args:
            seed: Random seed for reproducibility
            count: Number of URLs to generate
        """
        self.rng = random.Random(seed)
        self.count = count
        self._urls: list[str] = []
        self._behaviors: dict[str, URLBehavior] = {}
        self._index = 0
        self._generate_urls()

    def _generate_urls(self) -> None:
        """Generate URL set with various behaviors."""
        base = "https://httpbin.org"

        # Distribution of URL types (roughly):
        # 50% success (fast)
        # 10% success (slow)
        # 15% client errors (4xx)
        # 15% server errors (5xx)
        # 5% timeouts
        # 5% with body keywords

        url_specs = []

        # Fast successes (50%)
        for _ in range(int(self.count * 0.50)):
            url_specs.append(("success_fast", f"{base}/get"))

        # Slow successes (10%)
        for _ in range(int(self.count * 0.10)):
            delay = self.rng.choice([1, 2])  # 1-2 second delay
            url_specs.append(("success_slow", f"{base}/delay/{delay}"))

        # Client errors (15%)
        for _ in range(int(self.count * 0.15)):
            code = self.rng.choice([400, 401, 403, 404])
            url_specs.append(("client_error", f"{base}/status/{code}"))

        # Server errors (15%)
        for _ in range(int(self.count * 0.15)):
            code = self.rng.choice([500, 502, 503])
            url_specs.append(("server_error", f"{base}/status/{code}"))

        # Body keyword matches (5%)
        for _ in range(int(self.count * 0.05)):
            url_specs.append(("body_match", f"{base}/html"))

        # Slow with body match (5%)
        for _ in range(max(1, int(self.count * 0.05))):
            url_specs.append(("slow_body", f"{base}/delay/1"))

        # Shuffle
        self.rng.shuffle(url_specs)

        # Assign behaviors
        for url_type, url in url_specs:
            # Make URLs unique by adding query param
            unique_url = f"{url}?_id={len(self._urls)}"
            self._urls.append(unique_url)
            self._behaviors[unique_url] = self._create_behavior(url_type, url)

    def _create_behavior(self, url_type: str, base_url: str) -> URLBehavior:
        """Create expected behavior for URL type."""
        if url_type == "success_fast":
            return URLBehavior(
                status_code=200,
                min_latency_ms=0,
                should_retry=False,
                expected_callbacks=["on_success"],
                body_keywords=[],
                error_type=None
            )
        elif url_type == "success_slow":
            return URLBehavior(
                status_code=200,
                min_latency_ms=1000,
                should_retry=False,
                expected_callbacks=["on_success", "on_slow_response"],
                body_keywords=[],
                error_type=None
            )
        elif url_type == "client_error":
            code = int(base_url.split("/")[-1])
            return URLBehavior(
                status_code=code,
                min_latency_ms=0,
                should_retry=False,
                expected_callbacks=["on_client_error"],
                body_keywords=[],
                error_type=None
            )
        elif url_type == "server_error":
            code = int(base_url.split("/")[-1])
            return URLBehavior(
                status_code=code,
                min_latency_ms=0,
                should_retry=True,
                expected_callbacks=["on_server_error", "on_retry"],
                body_keywords=[],
                error_type=None
            )
        elif url_type == "body_match":
            return URLBehavior(
                status_code=200,
                min_latency_ms=0,
                should_retry=False,
                expected_callbacks=["on_success", "on_body_match"],
                body_keywords=["Herman"],  # httpbin.org/html contains "Herman Melville"
                error_type=None
            )
        elif url_type == "slow_body":
            return URLBehavior(
                status_code=200,
                min_latency_ms=1000,
                should_retry=False,
                expected_callbacks=["on_success", "on_slow_response"],
                body_keywords=[],
                error_type=None
            )
        else:
            # Default: success
            return URLBehavior(
                status_code=200,
                min_latency_ms=0,
                should_retry=False,
                expected_callbacks=["on_success"],
                body_keywords=[],
                error_type=None
            )

    def next_url(self) -> Optional[str]:
        """
        Get next URL to fetch.

        Returns:
            URL string, or None if all URLs consumed.
        """
        if self._index >= len(self._urls):
            return None
        url = self._urls[self._index]
        self._index += 1
        return url

    def get_behavior(self, url: str) -> Optional[URLBehavior]:
        """
        Get expected behavior for URL.

        Args:
            url: URL to look up

        Returns:
            URLBehavior if known, None otherwise.
        """
        return self._behaviors.get(url)

    def remaining(self) -> int:
        """Number of URLs remaining."""
        return len(self._urls) - self._index

    def total(self) -> int:
        """Total number of URLs."""
        return len(self._urls)

    def reset(self) -> None:
        """Reset to beginning of URL list."""
        self._index = 0

    def get_all_urls(self) -> list[str]:
        """Get all URLs (for validation)."""
        return self._urls.copy()


class ResponseValidator:
    """
    Validates that client invoked correct callbacks for each URL.

    Usage:
        validator = ResponseValidator(provider)
        # ... client runs and logs callbacks ...
        validator.add_callback(url, "on_success", {...})
        results = validator.validate()
    """

    def __init__(self, provider: URLProvider):
        self.provider = provider
        self._recorded: dict[str, list[str]] = {}  # url -> list of callback names

    def add_callback(self, url: str, callback_name: str) -> None:
        """Record that a callback was invoked for URL."""
        if url not in self._recorded:
            self._recorded[url] = []
        self._recorded[url].append(callback_name)

    def validate(self) -> dict:
        """
        Validate recorded callbacks against expected behaviors.

        Returns:
            Validation results with pass/fail for each URL.
        """
        results = {
            "total": self.provider.total(),
            "passed": 0,
            "failed": 0,
            "failures": []
        }

        for url in self.provider.get_all_urls():
            behavior = self.provider.get_behavior(url)
            recorded = set(self._recorded.get(url, []))
            expected = set(behavior.expected_callbacks) if behavior else set()

            # Check required callbacks present
            # Note: on_retry may appear multiple times, on_max_retries if exhausted
            required = {cb for cb in expected if cb not in ("on_retry", "on_max_retries")}
            missing = required - recorded

            if missing:
                results["failed"] += 1
                results["failures"].append({
                    "url": url,
                    "expected": list(expected),
                    "recorded": list(recorded),
                    "missing": list(missing)
                })
            else:
                results["passed"] += 1

        return results


if __name__ == "__main__":
    # Demo usage
    provider = URLProvider(seed=42, count=20)

    print(f"Generated {provider.total()} URLs")
    print()

    while (url := provider.next_url()) is not None:
        behavior = provider.get_behavior(url)
        print(f"URL: {url[:60]}...")
        print(f"  Status: {behavior.status_code}")
        print(f"  Retry: {behavior.should_retry}")
        print(f"  Callbacks: {behavior.expected_callbacks}")
        print()
