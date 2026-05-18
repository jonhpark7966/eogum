"""Backend access checks backed by Supabase profile flags."""

import logging

logger = logging.getLogger(__name__)


def is_admin_user(db, user_id: str) -> bool:
    """Return whether the current user is marked as an admin in profiles."""
    try:
        result = (
            db.table("profiles")
            .select("is_admin")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception:
        logger.exception("Failed to load admin flag for user %s", user_id)
        return False

    return bool(result.data and result.data.get("is_admin"))


def projects_query_for_user(db, user_id: str, columns: str = "*"):
    query = db.table("projects").select(columns)
    if not is_admin_user(db, user_id):
        query = query.eq("user_id", user_id)
    return query


def project_query_for_user(db, project_id: str, user_id: str, columns: str = "*"):
    return projects_query_for_user(db, user_id, columns).eq("id", project_id)
