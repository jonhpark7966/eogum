from supabase import Client, create_client

from eogum.config import settings

_client: Client | None = None


def get_db() -> Client:
    """Get Supabase client (service role for backend operations)."""
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client
