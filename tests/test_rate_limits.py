import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from nextlife.connectors.rate_limits import (
    USER_AGENTS,
    RateLimiter,
    RateLimitExceededError,
    get_rotated_headers,
)


def test_get_rotated_headers():
    seen_uas = set()
    for _ in range(len(USER_AGENTS) * 2):
        headers = get_rotated_headers({"X-Custom": "test"})
        assert "User-Agent" in headers
        assert headers["X-Custom"] == "test"
        seen_uas.add(headers["User-Agent"])
    assert len(seen_uas) == len(USER_AGENTS)


def test_rate_limiter_min_delay():
    limiter = RateLimiter()
    limiter.configure("test_mp", requests_per_minute=100, min_delay_seconds=0.1)

    # First request passes immediately
    limiter.acquire("test_mp", block=False)

    # Immediate second request should require wait time
    wait = limiter.calculate_wait_time("test_mp")
    assert wait > 0.0

    # Non-blocking acquire should raise RateLimitExceededError
    with pytest.raises(RateLimitExceededError) as exc_info:
        limiter.acquire("test_mp", block=False)
    assert exc_info.value.marketplace_key == "test_mp"
    assert exc_info.value.wait_seconds > 0.0

    # After sleeping min_delay, wait is 0
    time.sleep(0.12)
    assert limiter.calculate_wait_time("test_mp") == 0.0
    limiter.acquire("test_mp", block=False)


def test_rate_limiter_window_capacity():
    limiter = RateLimiter()
    # 3 requests per minute, 0 min delay
    limiter.configure("window_mp", requests_per_minute=3, min_delay_seconds=0.0)

    t0 = 1000.0
    # Simulate 3 requests in window
    for _ in range(3):
        assert limiter.calculate_wait_time("window_mp", now=t0) == 0.0
        # record manually in state
        limiter._states["window_mp"].history.append(t0)

    # 4th request must wait for the 1st request to expire (60s)
    wait = limiter.calculate_wait_time("window_mp", now=t0 + 10.0)
    assert abs(wait - 50.0) < 0.01


def test_rate_limiter_thread_safety():
    limiter = RateLimiter()
    limiter.configure("concurrent_mp", requests_per_minute=1000, min_delay_seconds=0.0)

    def worker():
        for _ in range(10):
            limiter.acquire("concurrent_mp", block=True)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker) for _ in range(5)]
        for f in futures:
            f.result()

    state = limiter._states["concurrent_mp"]
    assert len(state.history) == 50
