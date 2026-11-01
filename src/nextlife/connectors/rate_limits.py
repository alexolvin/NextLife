"""Marketplace rate limiting and request header rotation (TZ v2.0 Section I.2, VI.1).

DoD:
- Enforces requests_per_minute and min_delay_seconds from marketplace configuration.
- Sliding window algorithm prevents burst traffic and WAF/bot bans.
- Non-blocking mode raises RateLimitExceededError with wait_seconds.
- Thread-safe request synchronization.
- User-Agent and client header rotation across modern browser standards.
- Module size <= 400 lines.
"""

import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from nextlife.core.exceptions import NextLifeError


class RateLimitError(NextLifeError):
    """Base error for connector rate limits."""


class RateLimitExceededError(RateLimitError):
    """Raised when marketplace rate limit is exceeded in non-blocking mode."""

    def __init__(self, marketplace_key: str, wait_seconds: float) -> None:
        self.marketplace_key = marketplace_key
        self.wait_seconds = wait_seconds
        super().__init__(
            f"Rate limit exceeded for '{marketplace_key}'. Retry after {wait_seconds:.2f}s."
        )


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

_UA_CYCLE = itertools.cycle(USER_AGENTS)
_UA_LOCK = Lock()


def get_rotated_headers(extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    """Retrieve rotated User-Agent and standard browser headers."""
    with _UA_LOCK:
        ua = next(_UA_CYCLE)

    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "cs,en-US;q=0.8,en;q=0.6",
        "DNT": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


@dataclass
class MarketplaceThrottleState:
    requests_per_minute: int = 20
    min_delay_seconds: float = 2.0
    last_request_time: float = 0.0
    history: deque[float] = field(default_factory=deque)


class RateLimiter:
    """Thread-safe rate limiter managing multiple marketplace quotas."""

    def __init__(self) -> None:
        self._states: dict[str, MarketplaceThrottleState] = {}
        self._lock = Lock()

    def configure(
        self,
        marketplace_key: str,
        requests_per_minute: int = 20,
        min_delay_seconds: float = 2.0,
    ) -> None:
        """Register or update limits for a specific marketplace."""
        with self._lock:
            self._states[marketplace_key] = MarketplaceThrottleState(
                requests_per_minute=requests_per_minute,
                min_delay_seconds=min_delay_seconds,
            )

    def calculate_wait_time(self, marketplace_key: str, now: float | None = None) -> float:
        """Calculate necessary wait time in seconds before next request is permitted."""
        t_now = now if now is not None else time.time()
        with self._lock:
            state = self._states.get(marketplace_key)
            if not state:
                return 0.0

            # 1. Clean history older than 60s
            window_start = t_now - 60.0
            while state.history and state.history[0] <= window_start:
                state.history.popleft()

            wait_for_min_delay = 0.0
            if state.last_request_time > 0:
                elapsed = t_now - state.last_request_time
                if elapsed < state.min_delay_seconds:
                    wait_for_min_delay = state.min_delay_seconds - elapsed

            wait_for_window = 0.0
            if len(state.history) >= state.requests_per_minute:
                oldest_in_window = state.history[0]
                wait_for_window = (oldest_in_window + 60.0) - t_now

            return max(0.0, wait_for_min_delay, wait_for_window)

    def acquire(self, marketplace_key: str, block: bool = True) -> float:
        """Acquire permission to execute a request. Sleeps if block=True, else raises on limit."""
        while True:
            t_now = time.time()
            wait_time = self.calculate_wait_time(marketplace_key, t_now)
            if wait_time > 0.001:
                if not block:
                    raise RateLimitExceededError(marketplace_key, wait_time)
                time.sleep(wait_time)
                continue

            with self._lock:
                state = self._states.setdefault(marketplace_key, MarketplaceThrottleState())
                t_record = time.time()
                state.last_request_time = t_record
                state.history.append(t_record)
                return wait_time


# Singleton global limiter instance
global_rate_limiter = RateLimiter()
