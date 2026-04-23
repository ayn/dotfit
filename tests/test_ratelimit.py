"""Tests for Strava rate-limit tracker."""

from dotfit.ratelimit import RateLimiter


def test_initial_state():
    rl = RateLimiter(short_limit=90, daily_limit=900)
    assert rl.can_proceed()
    assert not rl.short_window_exhausted()
    assert not rl.daily_exhausted()
    assert rl.wait_time() == 0


def test_update_from_headers():
    rl = RateLimiter()
    rl.update_from_headers({
        "X-RateLimit-Limit": "600,30000",
        "X-RateLimit-Usage": "50,500",
    })
    assert rl.short_usage == 50
    assert rl.daily_usage == 500
    assert rl.short_limit_actual == 600
    assert rl.daily_limit_actual == 30000
    assert rl.can_proceed()


def test_update_from_lowercase_headers():
    rl = RateLimiter()
    rl.update_from_headers({
        "x-ratelimit-limit": "600,30000",
        "x-ratelimit-usage": "100,1000",
    })
    assert rl.short_usage == 100
    assert rl.daily_usage == 1000


def test_short_window_exhausted():
    rl = RateLimiter(short_limit=90, daily_limit=900)
    rl.update_from_headers({
        "X-RateLimit-Limit": "600,30000",
        "X-RateLimit-Usage": "90,100",
    })
    assert rl.short_window_exhausted()
    assert not rl.can_proceed()
    assert rl.wait_time() > 0


def test_daily_exhausted():
    rl = RateLimiter(short_limit=90, daily_limit=900)
    rl.update_from_headers({
        "X-RateLimit-Limit": "600,30000",
        "X-RateLimit-Usage": "10,900",
    })
    assert rl.daily_exhausted()
    assert not rl.can_proceed()


def test_under_conservative_but_over_actual():
    """Even if under actual Strava limits, we stop at our conservative threshold."""
    rl = RateLimiter(short_limit=90, daily_limit=900)
    rl.update_from_headers({
        "X-RateLimit-Limit": "600,30000",
        "X-RateLimit-Usage": "95,100",
    })
    # Over our short limit (90) but under Strava's (600)
    assert not rl.can_proceed()


def test_status_string():
    rl = RateLimiter(short_limit=90, daily_limit=900)
    rl.update_from_headers({
        "X-RateLimit-Limit": "600,30000",
        "X-RateLimit-Usage": "42,123",
    })
    s = rl.status()
    assert "42" in s
    assert "90" in s
    assert "123" in s
    assert "900" in s


def test_malformed_headers_dont_crash():
    rl = RateLimiter()
    rl.update_from_headers({"X-RateLimit-Limit": "garbage"})
    rl.update_from_headers({"X-RateLimit-Usage": ""})
    rl.update_from_headers({})
    # Should not raise — just log warnings
    assert rl.can_proceed()


def test_wait_time_positive_when_exhausted():
    rl = RateLimiter(short_limit=5, daily_limit=900)
    rl.update_from_headers({
        "X-RateLimit-Limit": "600,30000",
        "X-RateLimit-Usage": "5,10",
    })
    wait = rl.wait_time()
    assert 0 < wait <= 905  # at most 15 min + 5s buffer
