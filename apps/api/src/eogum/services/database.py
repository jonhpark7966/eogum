import logging
import time
from collections.abc import Callable
from typing import TypeVar

import httpx
from postgrest.exceptions import APIError
from supabase import Client, create_client

from eogum.config import settings

logger = logging.getLogger(__name__)

_client: Client | None = None
T = TypeVar("T")

_SUPABASE_MAX_RETRIES = 3
_SUPABASE_RETRY_DELAY_SECONDS = 5


def get_db() -> Client:
    """Get Supabase client (service role for backend operations)."""
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client


def _is_retryable_supabase_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPError):
        return True
    if isinstance(exc, APIError):
        try:
            payload = exc.json()
        except Exception:
            payload = {}
        code = payload.get("code") if isinstance(payload, dict) else None
        if isinstance(code, str) and code.isdigit():
            return int(code) >= 500
    return False


def execute_with_retry(operation: Callable[[], T], *, operation_name: str) -> T:
    """Run a Supabase/PostgREST operation with retries for transient transport/server errors."""
    max_attempts = _SUPABASE_MAX_RETRIES + 1
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            retryable = _is_retryable_supabase_error(exc)
            is_last_attempt = attempt == max_attempts
            if not retryable or is_last_attempt:
                logger.exception(
                    "Supabase operation failed: %s attempt=%s/%s retryable=%s",
                    operation_name,
                    attempt,
                    max_attempts,
                    retryable,
                )
                raise

            logger.warning(
                "Supabase operation failed; retrying in %ss: %s attempt=%s/%s error=%r",
                _SUPABASE_RETRY_DELAY_SECONDS,
                operation_name,
                attempt,
                max_attempts,
                exc,
            )
            time.sleep(_SUPABASE_RETRY_DELAY_SECONDS)

    raise RuntimeError(f"unreachable Supabase retry state: {operation_name}")
