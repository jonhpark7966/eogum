from fastapi import APIRouter, Depends

from eogum.auth import get_user_id
from eogum.models.schemas import CreditBalanceResponse, CreditTransactionResponse
from eogum.services.credit import get_balance
from eogum.services.database import get_db

router = APIRouter(prefix="/credits", tags=["credits"])


@router.get("", response_model=CreditBalanceResponse)
def get_credit_balance(user_id: str = Depends(get_user_id)):
    return get_balance(user_id)


@router.get("/transactions", response_model=list[CreditTransactionResponse])
def get_transactions(user_id: str = Depends(get_user_id), limit: int = 50, offset: int = 0):
    db = get_db()
    result = (
        db.table("credit_transactions")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return result.data
