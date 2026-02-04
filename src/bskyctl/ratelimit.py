from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

try:
    import fcntl  # unix-only; used for cross-process throttling
except Exception:  # pragma: no cover
    fcntl = None

from .utils import atomic_write_json

CACHE_DIR = Path.home() / ".cache" / "bsky"
RATE_STATE_DIR = CACHE_DIR / "ratelimit"

# Client-side throttling (helps when running multiple CLI calls in parallel).
# Defaults are conservative vs the hosted PDS limit (3000 requests / 5 minutes).
REQ_RPS = float(os.getenv("BSKY_REQ_RPS", "8"))  # tokens/sec
REQ_BURST = float(os.getenv("BSKY_REQ_BURST", "16"))  # max accumulated tokens

_THROTTLE_ENABLED = True


def set_throttle_enabled(enabled: bool) -> None:
    global _THROTTLE_ENABLED
    _THROTTLE_ENABLED = bool(enabled)


class SharedTokenBucket:
    """A tiny cross-process token bucket (best effort).

    Purpose: avoid smashing hosted PDS API limits when running multiple CLI
    invocations in parallel (e.g. many `bskyctl search ...` calls).

    Uses flock when available; otherwise falls back to per-process throttling.
    """

    def __init__(self, *, key: str, refill_per_s: float, capacity: float):
        self.key = key
        self.refill_per_s = max(0.001, float(refill_per_s))
        self.capacity = max(1.0, float(capacity))
        RATE_STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state_path = RATE_STATE_DIR / f"{key}.json"
        self.lock_path = RATE_STATE_DIR / f"{key}.lock"
        self._local_tokens = self.capacity
        self._local_updated = time.time()

    def acquire(self, tokens: float = 1.0) -> None:
        if tokens <= 0:
            return

        # No flock? -> simple local token bucket.
        if fcntl is None:
            self._acquire_local(tokens)
            return

        while True:
            now = time.time()
            with self.lock_path.open("a+") as lockf:
                fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
                state = {"tokens": self.capacity, "updated": now}
                if self.state_path.exists():
                    try:
                        state = json.loads(self.state_path.read_text(encoding="utf-8"))
                    except Exception:
                        state = {"tokens": self.capacity, "updated": now}

                prev_tokens = float(state.get("tokens", self.capacity))
                prev_updated = float(state.get("updated", now))
                dt = max(0.0, now - prev_updated)
                avail = min(self.capacity, prev_tokens + dt * self.refill_per_s)

                if avail >= tokens:
                    new_state = {"tokens": avail - tokens, "updated": now}
                    atomic_write_json(self.state_path, new_state)
                    fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
                    return

                # Not enough tokens yet.
                needed = tokens - avail
                wait_s = needed / self.refill_per_s
                new_state = {"tokens": avail, "updated": now}
                atomic_write_json(self.state_path, new_state)
                fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

            time.sleep(min(2.0, max(0.01, wait_s)))

    def _acquire_local(self, tokens: float) -> None:
        while True:
            now = time.time()
            dt = max(0.0, now - self._local_updated)
            self._local_tokens = min(self.capacity, self._local_tokens + dt * self.refill_per_s)
            self._local_updated = now
            if self._local_tokens >= tokens:
                self._local_tokens -= tokens
                return
            wait_s = (tokens - self._local_tokens) / self.refill_per_s
            time.sleep(min(2.0, max(0.01, wait_s)))


_REQ_BUCKET: SharedTokenBucket | None = None


def _get_req_bucket() -> SharedTokenBucket:
    global _REQ_BUCKET
    if _REQ_BUCKET is None:
        _REQ_BUCKET = SharedTokenBucket(key="req", refill_per_s=REQ_RPS, capacity=REQ_BURST)
    return _REQ_BUCKET


def throttle_req(tokens: float = 1.0) -> None:
    if not _THROTTLE_ENABLED:
        return
    _get_req_bucket().acquire(tokens)


def call_with_read_backoff(fn, *, attempts: int = 3):
    """Generic wrapper for read-ish calls: throttle + retry on 429."""

    throttle_req(1.0)
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if is_rate_limited(e) and attempt < max(0, int(attempts) - 1):
                wait_s = random.uniform(2.0, 5.0) * (2**attempt)
                time.sleep(wait_s)
                attempt += 1
                continue
            raise


def call_with_write_backoff(fn, *, attempts: int = 3):
    """Generic wrapper for write-ish calls: throttle + longer retry on 429."""

    throttle_req(1.0)
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if is_rate_limited(e) and attempt < max(0, int(attempts) - 1):
                wait_s = random.uniform(15.0, 35.0) * (1.6**attempt)
                print(f"Rate limited; backing off {wait_s:.1f}s ...", file=sys.stderr)
                time.sleep(wait_s)
                attempt += 1
                continue
            raise


def is_rate_limited(err: Exception) -> bool:
    msg = str(err)
    return (
        "429" in msg
        or "RateLimit" in msg
        or "rate limit" in msg.lower()
        or "TooManyRequests" in msg
        or "ratelimit" in msg.lower()
    )


def is_already_exists(err: Exception) -> bool:
    msg = str(err)
    return (
        "AlreadyExists" in msg
        or "already exists" in msg.lower()
        or "Duplicate" in msg
        or "DuplicateRecord" in msg
        or "RecordAlreadyExists" in msg
    )
