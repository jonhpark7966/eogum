from fastapi import HTTPException, status

from eogum.services.database import get_db


def _first_row(data) -> dict | None:
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def get_balance(user_id: str) -> dict:
    """Get user's credit balance."""
    db = get_db()
    result = db.table("credits").select("*").eq("user_id", user_id).single().execute()
    data = result.data
    return {
        "balance_seconds": data["balance_seconds"],
        "held_seconds": data["held_seconds"],
        "available_seconds": data["balance_seconds"] - data["held_seconds"],
    }


def hold_credits(user_id: str, seconds: int, job_id: str | None = None) -> None:
    """Hold credits before processing starts. Raises if insufficient."""
    if seconds <= 0:
        return

    db = get_db()
    result = db.rpc(
        "hold_credits_atomic",
        {"p_user_id": user_id, "p_seconds": seconds, "p_job_id": job_id},
    ).execute()
    if _first_row(result.data):
        return

    balance = get_balance(user_id)
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=f"크레딧이 부족합니다. 필요: {seconds}초, 사용 가능: {balance['available_seconds']}초",
    )


def confirm_usage(user_id: str, seconds: int, job_id: str | None = None) -> None:
    """Confirm credit usage after successful processing. Converts hold to actual deduction."""
    if seconds <= 0:
        return

    db = get_db()
    result = db.rpc(
        "confirm_usage_atomic",
        {"p_user_id": user_id, "p_seconds": seconds, "p_job_id": job_id},
    ).execute()
    if not _first_row(result.data):
        raise RuntimeError(f"Failed to confirm credit usage for user {user_id}, job {job_id}")


def release_hold(user_id: str, seconds: int, job_id: str | None = None) -> None:
    """Release held credits after processing failure."""
    if seconds <= 0:
        return

    db = get_db()
    result = db.rpc(
        "release_hold_atomic",
        {"p_user_id": user_id, "p_seconds": seconds, "p_job_id": job_id},
    ).execute()
    if not _first_row(result.data):
        raise RuntimeError(f"Failed to release credit hold for user {user_id}, job {job_id}")
