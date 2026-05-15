import logging
import time

import httpx
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from eogum.config import settings

logger = logging.getLogger(__name__)

_client: Client | None = None

_MAX_SUPABASE_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 0.25
_RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS"}
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.NetworkError,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.TimeoutException,
)


class RetryingSupabaseHttpClient(httpx.Client):
    """HTTPX client with bounded retries for transient Supabase read failures."""

    def request(self, method: str, url, **kwargs) -> httpx.Response:  # type: ignore[override]
        normalized_method = method.upper()
        should_retry = normalized_method in _RETRYABLE_METHODS
        attempts = _MAX_SUPABASE_ATTEMPTS if should_retry else 1

        for attempt in range(1, attempts + 1):
            try:
                response = super().request(method, url, **kwargs)
                if (
                    response.status_code in _RETRYABLE_STATUS_CODES
                    and attempt < attempts
                ):
                    response.close()
                    self._sleep_before_retry(attempt, response.status_code)
                    continue
                return response
            except _RETRYABLE_EXCEPTIONS as exc:
                if attempt >= attempts:
                    raise
                self._sleep_before_retry(attempt, exc.__class__.__name__)

        raise RuntimeError("unreachable Supabase retry state")

    @staticmethod
    def _sleep_before_retry(attempt: int, reason: object) -> None:
        delay = _RETRY_BACKOFF_SECONDS * attempt
        logger.warning(
            "Transient Supabase HTTP failure; retrying attempt %s/%s after %.2fs (%s)",
            attempt + 1,
            _MAX_SUPABASE_ATTEMPTS,
            delay,
            reason,
        )
        time.sleep(delay)


def _create_db_client() -> Client:
    http_client = RetryingSupabaseHttpClient(
        timeout=httpx.Timeout(120.0),
        follow_redirects=True,
        http2=False,
    )
    return create_client(
        settings.supabase_url,
        settings.supabase_service_key,
        options=SyncClientOptions(httpx_client=http_client),
    )


def get_db() -> Client:
    """Get Supabase client (service role for backend operations)."""
    global _client
    if _client is None:
        _client = _create_db_client()
    return _client
