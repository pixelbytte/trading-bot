"""
Exponential backoff retry wrapper for external API calls.
Use for any call that can fail transiently (network blips, rate limits).
"""

import time
from utils.logger import warning


def with_retry(fn, retries=3, delay=2.0, backoff=2.0, source=""):
    """
    Call fn(). On exception, wait and retry up to `retries` times.
    Raises the last exception if all attempts fail.

    Args:
        fn: Zero-arg callable to attempt.
        retries: Total attempts (including the first).
        delay: Seconds before first retry.
        backoff: Multiply delay by this after each failure.
        source: Tag for log messages.
    """
    last_exc = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                wait = delay * (backoff ** attempt)
                warning(
                    f"Attempt {attempt + 1}/{retries} failed: {e}. "
                    f"Retrying in {wait:.0f}s...",
                    source=source,
                )
                time.sleep(wait)
    raise last_exc
