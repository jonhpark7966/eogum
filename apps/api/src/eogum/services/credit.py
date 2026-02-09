from fastapi import HTTPException, status

from eogum.services.database import get_db


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
    db = get_db()
    balance = get_balance(user_id)

    if balance["available_seconds"] < seconds:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"크레딧이 부족합니다. 필요: {seconds}초, 사용 가능: {balance['available_seconds']}초",
        )

    # Update held amount
    db.table("credits").update({
        "held_seconds": balance["held_seconds"] + seconds,
    }).eq("user_id", user_id).execute()

    # Record transaction
    db.table("credit_transactions").insert({
        "user_id": user_id,
        "amount_seconds": -seconds,
        "type": "hold",
        "job_id": job_id,
        "description": f"처리 시작 홀딩 ({seconds}초)",
    }).execute()


def confirm_usage(user_id: str, seconds: int, job_id: str | None = None) -> None:
    """Confirm credit usage after successful processing. Converts hold to actual deduction."""
    db = get_db()
    credit = db.table("credits").select("*").eq("user_id", user_id).single().execute().data

    db.table("credits").update({
        "balance_seconds": credit["balance_seconds"] - seconds,
        "held_seconds": credit["held_seconds"] - seconds,
    }).eq("user_id", user_id).execute()

    db.table("credit_transactions").insert({
        "user_id": user_id,
        "amount_seconds": -seconds,
        "type": "usage",
        "job_id": job_id,
        "description": f"처리 완료 ({seconds}초 사용)",
    }).execute()


def release_hold(user_id: str, seconds: int, job_id: str | None = None) -> None:
    """Release held credits after processing failure."""
    db = get_db()
    credit = db.table("credits").select("*").eq("user_id", user_id).single().execute().data

    db.table("credits").update({
        "held_seconds": max(0, credit["held_seconds"] - seconds),
    }).eq("user_id", user_id).execute()

    db.table("credit_transactions").insert({
        "user_id": user_id,
        "amount_seconds": seconds,
        "type": "hold_release",
        "job_id": job_id,
        "description": f"처리 실패 홀딩 해제 ({seconds}초 복구)",
    }).execute()
