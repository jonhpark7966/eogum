from fastapi import APIRouter

from eogum.models.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", version="0.1.0")
