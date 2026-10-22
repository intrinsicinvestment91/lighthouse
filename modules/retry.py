"""
Reusable tenacity retry decorators for the Lighthouse pipeline.

Apply @http_retry to httpx-based fetchers and @ws_retry to websocket publishers.
Both reraise the final exception after exhausting attempts so callers can log it.
"""

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

http_retry = retry(
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)

ws_retry = retry(
    wait=wait_exponential(min=1, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
